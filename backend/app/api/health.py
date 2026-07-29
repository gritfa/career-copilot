"""健康端点（docs/02-architecture.md 第 7 节语义）。

- ``/health/live``：进程能响应即 200，不检查外部依赖。
- ``/health/ready``：数据库、Redis、Alembic 迁移版本全部可用才 200，否则 503 + 结构化原因。
- ``/health/capabilities``：各能力独立状态；未经真实验证一律 ``not_verified``，绝不虚标 ready。
"""

import asyncio
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings

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
CAPABILITIES: dict[str, str] = {
    "job_source:fixture_a": "not_verified",
    "job_source:fixture_b": "not_verified",
    "job_source:boss": "import_only",
    "deepseek_generation": "not_verified",
    "qwen_fallback": "not_verified",
    "aliyun_embedding": "not_verified",
    "matching_basic": "ready",
    "resume_parse_pdf": "ready",
    "resume_parse_docx": "ready",
    "docx_export": "not_verified",
    "pdf_export": "not_verified",
    "email_magic_link": "not_verified",
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
    """能力矩阵：服务存活不代表任何来源/模型/导出能力可用。"""
    return {"capabilities": dict(CAPABILITIES)}
