"""来源注册表：种子 job_sources 行 + source_key → Adapter 映射。

能力状态如实：fixture 连接器 policy_status=not_verified；
BOSS 无允许的自动访问方式 → link_out/import_only，无 Adapter，绝不抓取。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.models import JobSource
from app.jobs.adapters.base import JobSourceAdapter
from app.jobs.adapters.boss import BOSS_BASE_URL
from app.jobs.adapters.fixture_company import fixture_adapter_a, fixture_adapter_b
from app.jobs.constants import (
    SOURCE_KEY_BOSS,
    SOURCE_KEY_FIXTURE_A,
    SOURCE_KEY_FIXTURE_B,
    SOURCE_KEY_USER_IMPORT,
)

# 连续失败熔断阈值（rate_limit_config 可覆盖）
DEFAULT_CIRCUIT_FAILURE_THRESHOLD = 3

SOURCE_SEEDS: list[dict[str, Any]] = [
    {
        "source_key": SOURCE_KEY_FIXTURE_A,
        "name": "星岚科技招聘官网（合成 fixture）",
        "source_type": "company_site",
        "base_url": "https://careers.xinglan-fixture.example",
        "status": "enabled",
        "rate_limit_config": {"per_minute": 120, "burst": 30, "circuit_failure_threshold": 3},
        "capabilities_json": {
            "mode": "fixture",
            "policy_status": "not_verified",
            "notes": "合成数据连接器；真实来源政策调查未完成，禁止抓取。",
        },
    },
    {
        "source_key": SOURCE_KEY_FIXTURE_B,
        "name": "云图智能招聘官网（合成 fixture）",
        "source_type": "company_site",
        "base_url": "https://jobs.yuntu-fixture.example",
        "status": "enabled",
        "rate_limit_config": {"per_minute": 120, "burst": 30, "circuit_failure_threshold": 3},
        "capabilities_json": {
            "mode": "fixture",
            "policy_status": "not_verified",
            "notes": "合成数据连接器；真实来源政策调查未完成，禁止抓取。",
        },
    },
    {
        "source_key": SOURCE_KEY_BOSS,
        "name": "BOSS 直聘",
        "source_type": "platform",
        "base_url": BOSS_BASE_URL,
        "status": "enabled",
        "rate_limit_config": {},
        "capabilities_json": {
            "mode": "link_out/import_only",
            "policy_status": "not_verified",
            "notes": "无允许的自动访问方式：只生成搜索跳转 URL + 用户粘贴导入，无采集。",
        },
    },
    {
        "source_key": SOURCE_KEY_USER_IMPORT,
        "name": "用户导入",
        "source_type": "user_import",
        "base_url": None,
        "status": "enabled",
        "rate_limit_config": {},
        "capabilities_json": {"mode": "import_only", "policy_status": "verified"},
    },
]

_ADAPTER_FACTORIES = {
    SOURCE_KEY_FIXTURE_A: fixture_adapter_a,
    SOURCE_KEY_FIXTURE_B: fixture_adapter_b,
}


def get_adapter(source_key: str) -> JobSourceAdapter | None:
    """返回来源 Adapter；link_out/import_only 来源（boss/user_import）无 Adapter。"""
    factory = _ADAPTER_FACTORIES.get(source_key)
    return factory() if factory else None


def _seed_stmt():
    now = datetime.now(UTC)
    stmt = pg_insert(JobSource.__table__).values(
        [
            {
                "id": uuid.uuid4(),
                **seed,
                "consecutive_failures": 0,
                "created_at": now,
                "updated_at": now,
            }
            for seed in SOURCE_SEEDS
        ]
    )
    return stmt.on_conflict_do_nothing(index_elements=["source_key"])


def seed_sources_sync(db: Session) -> None:
    """幂等注册全部来源（同步会话：Celery 任务 / 线程内导入管道用）。"""
    db.execute(_seed_stmt())
    db.flush()


async def seed_sources(db: AsyncSession) -> None:
    """幂等注册全部来源（异步会话：管理端只读视图等）。"""
    await db.execute(_seed_stmt())
    await db.flush()


def get_source_by_key_sync(db: Session, source_key: str) -> JobSource | None:
    return db.execute(
        select(JobSource).where(JobSource.source_key == source_key)
    ).scalar_one_or_none()
