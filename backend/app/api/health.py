"""健康端点（docs/02-architecture.md 第 7 节语义）。

- ``/health/live``：进程能响应即 200，不检查外部依赖。
- ``/health/ready``：数据库、Redis、Alembic 迁移版本全部可用才 200，否则 503 + 结构化原因。
- ``/health/capabilities``：各能力独立状态；未经真实验证一律 ``not_verified``，绝不虚标 ready。
"""

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.integrations.llm_gateway import DeepSeekAdapter

router = APIRouter(prefix="/health", tags=["health"])

_ALEMBIC_DIR = Path(__file__).resolve().parents[2] / "alembic"

# 能力矩阵：状态只允许在真实验证通过后由对应阶段改为 ready / degraded / disabled。
# 阶段 3：resume_parse_pdf / resume_parse_docx 经本机真实容器集成测试
# （合成 PDF/DOCX 上传 → Celery 解析 → 候选 → 确认全流程）验证通过，标记 ready。
# 阶段 4：fixture 连接器只是合成数据（ADR-001 D1，真实来源政策调查未完成），
# 如实标 not_verified；BOSS 无允许的自动访问方式，只有 link-out/导入 → import_only。
# 模型/导出等能力仍未真实验证，保持 not_verified。
# 阶段 5：matching_basic（硬条件 + 确定性向量召回 + 规则评分 + 反馈）经本机
# 真实 PG(pgvector)/Redis 容器集成测试验证 → ready；
# aliyun_embedding 无真实 API key，保持 not_verified（当前用确定性合成 Adapter）。
# 阶段 6（ADR-001 裁剪）：standard_analysis 管道（Model Gateway + 单模型标准分析）
# 经集成测试打通，但无真实 DEEPSEEK_API_KEY，只有确定性合成 Adapter 产出
# （报告如实标注 not_verified）→ 能力保持 not_verified；多 Agent（LangGraph）推迟。
# 阶段 11 P0（2026-07-30，docs/16）：真实 DeepSeek 三链路实跑验证——
# standard_analysis 管线（编排+授权门控+证据校验+落库）与 DOCX/PDF 导出
# 经真实模型端到端跑通 → ready。注意：ready 指管线本身；当前产出是否来自
# 真实模型，由下方 deepseek_generation 三态（configured/runtime）如实反映。
CAPABILITIES: dict[str, str] = {
    "job_source:fixture_a": "not_verified",
    "job_source:fixture_b": "not_verified",
    "job_source:boss": "import_only",
    "matching_basic": "ready",
    "standard_analysis": "ready",
    "resume_parse_pdf": "ready",
    "resume_parse_docx": "ready",
    "docx_export": "ready",
    "pdf_export": "ready",
    "email_magic_link": "not_verified",
}

# ---------------- 模型能力三态（docs/15 P0 第 3 条） ----------------
#
# 模型能力（deepseek_generation / qwen_fallback / aliyun_embedding）不再用单一
# 字符串，而是三个相互独立的维度：
#   1. configured      —— 当前进程环境里有 key（不代表 key 有效）；
#   2. runtime         —— 最近一次运行健康探测（GET /models，TTL 缓存）的结果：
#                         available / unavailable / not_probed；
#   3. last_verified   —— 历史验证证据：某日实跑全链路成功并留档 docs 的记录。
#                         **只是历史证据，绝不因某台机器成功跑过一次就把当前
#                         状态永久写成 verified**——当前可用性只看前两态。
# 汇总 status 只由前两态推导：not_configured / configured / available / unavailable。
_MODEL_PROBE_TTL_SECONDS = 600.0

# 历史验证证据（脱敏留档见 docs/16-real-model-verification.md）；
# 未验证的能力必须为 None，不许占位。
MODEL_VERIFICATION_EVIDENCE: dict[str, dict[str, str] | None] = {
    # 2026-07-30 本机 demo 环境实跑：标准分析 + 定制简历 + 无效 key 失败路径
    # 三链路全部通过（真实 token 用量与结论见 docs/16）。这只是历史证据。
    "deepseek_generation": {
        "verified_at": "2026-07-30",
        "model_id": "deepseek-chat",
        "evidence": "docs/16-real-model-verification.md",
    },
    "qwen_fallback": None,
    # Embedding 仍为确定性合成向量，从未真实验证（docs/15 P0 第 5 条）
    "aliyun_embedding": None,
}

# 探测结果缓存：capability -> (wall_time, "available"/"unavailable")
_probe_cache: dict[str, tuple[float, str]] = {}


def reset_probe_cache() -> None:
    """测试与进程内配置变更后清空运行探测缓存。"""
    _probe_cache.clear()


def _deepseek_runtime(configured: bool) -> tuple[str, str | None]:
    """运行可用探测（带 TTL 缓存）；未配置时不发任何网络请求。"""
    if not configured:
        return "not_probed", None
    cached = _probe_cache.get("deepseek_generation")
    now = time.time()
    if cached is not None and now - cached[0] < _MODEL_PROBE_TTL_SECONDS:
        checked_at = datetime.fromtimestamp(cached[0], tz=UTC).isoformat()
        return cached[1], checked_at
    status = "available" if DeepSeekAdapter().probe_runtime() else "unavailable"
    _probe_cache["deepseek_generation"] = (now, status)
    return status, datetime.fromtimestamp(now, tz=UTC).isoformat()


def _summary_status(configured: bool, runtime: str) -> str:
    if not configured:
        return "not_configured"
    if runtime == "available":
        return "available"
    if runtime == "unavailable":
        return "unavailable"
    return "configured"


def _model_capabilities() -> dict[str, dict[str, Any]]:
    settings = get_settings()
    deepseek_configured = bool(settings.deepseek_api_key)
    runtime, checked_at = _deepseek_runtime(deepseek_configured)
    return {
        "deepseek_generation": {
            "status": _summary_status(deepseek_configured, runtime),
            "configured": deepseek_configured,
            "runtime": runtime,
            "runtime_checked_at": checked_at,
            # 无 key 时 Gateway 自动落回确定性合成实现（如实声明当前产出来源）
            "active_adapter": "deepseek" if deepseek_configured else "synthetic",
            "last_verified": MODEL_VERIFICATION_EVIDENCE["deepseek_generation"],
        },
        "qwen_fallback": {
            "status": "not_configured",
            "configured": False,
            "runtime": "not_probed",
            "runtime_checked_at": None,
            "active_adapter": None,  # 备用供应商未实现 Adapter
            "last_verified": MODEL_VERIFICATION_EVIDENCE["qwen_fallback"],
        },
        "aliyun_embedding": {
            "status": _summary_status(bool(settings.dashscope_api_key), "not_probed"),
            "configured": bool(settings.dashscope_api_key),
            "runtime": "not_probed",
            "runtime_checked_at": None,
            # Embedding 仍为确定性合成向量（docs/15 P0 第 5 条，如实标注）
            "active_adapter": "synthetic",
            "last_verified": MODEL_VERIFICATION_EVIDENCE["aliyun_embedding"],
        },
    }


def _safe_reason(exc: BaseException) -> str:
    """构造不泄漏 DSN/密码的失败原因（只用异常类型名）。"""
    return f"{type(exc).__name__}: connection check failed"


async def _check_database() -> dict[str, Any]:
    """连接 PostgreSQL 并读取 alembic_version，返回 db 与迁移两项结果。"""
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=False)
    try:
        async with asyncio.timeout(settings.health_check_timeout_seconds):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                db_check: dict[str, Any] = {"status": "ok"}
                try:
                    result = await conn.execute(text("SELECT version_num FROM alembic_version"))
                    row = result.first()
                    current = row[0] if row else None
                except Exception:
                    current = None
                migration_check = _check_migration_version(current)
        return {"database": db_check, "migrations": migration_check}
    except Exception as exc:
        reason = _safe_reason(exc)
        return {
            "database": {"status": "failed", "reason": reason},
            "migrations": {"status": "failed", "reason": "database unreachable"},
        }
    finally:
        await engine.dispose()


def _check_migration_version(current: str | None) -> dict[str, Any]:
    """比较数据库迁移版本与代码里的 Alembic head。"""
    try:
        head = ScriptDirectory(str(_ALEMBIC_DIR)).get_current_head()
    except Exception as exc:
        return {"status": "failed", "reason": _safe_reason(exc)}
    if current is None:
        return {
            "status": "failed",
            "reason": "alembic_version not found; run migrations",
            "expected": head,
        }
    if current != head:
        return {
            "status": "failed",
            "reason": "migration version mismatch",
            "current": current,
            "expected": head,
        }
    return {"status": "ok", "version": current}


async def _check_redis() -> dict[str, Any]:
    """PING Redis。"""
    settings = get_settings()
    client = aioredis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.health_check_timeout_seconds,
        socket_timeout=settings.health_check_timeout_seconds,
    )
    try:
        async with asyncio.timeout(settings.health_check_timeout_seconds):
            await client.ping()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "failed", "reason": _safe_reason(exc)}
    finally:
        await client.aclose()


@router.get("/live")
async def live() -> dict[str, str]:
    """进程存活探针：能执行到这里就是 200，不查任何外部依赖。"""
    return {"status": "alive"}


@router.get("/ready")
async def ready(response: Response) -> dict[str, Any]:
    """就绪探针：DB、Redis、迁移版本全部 ok 才 200，否则 503。"""
    db_results, redis_result = await asyncio.gather(_check_database(), _check_redis())
    checks: dict[str, Any] = {
        "database": db_results["database"],
        "migrations": db_results["migrations"],
        "redis": redis_result,
    }
    all_ok = all(item.get("status") == "ok" for item in checks.values())
    if not all_ok:
        response.status_code = 503
    return {"status": "ready" if all_ok else "not_ready", "checks": checks}


@router.get("/capabilities")
async def capabilities() -> dict[str, Any]:
    """能力矩阵：服务存活不代表任何来源/模型/导出能力可用。

    模型能力为三态对象（configured / runtime / last_verified），
    其余能力保持字符串状态。
    """
    merged: dict[str, Any] = dict(CAPABILITIES)
    merged.update(_model_capabilities())
    return {"capabilities": merged}
