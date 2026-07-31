"""阶段 11 P1 集成测试：data_origin 标注 + 一键 demo seed（真实 PG/Redis/Mailpit）。

覆盖：
- data_origin 三值推导（connector / user_import / synthetic_seed）与迁移回填约束；
- 推荐列表/详情 API 透出 data_origin（前端「合成示例」徽标的数据依据）；
- seed_jobs_sync 幂等（重复执行零新增，含快照不膨胀）；
- run_demo_seed 全流程：正常注册（一次性邀请码 + Mailpit magic link，无后门）、
  事实库、方案、实跑推荐，重复执行幂等。
"""

import asyncio

from sqlalchemy import func, select

from app.db.models import (
    CanonicalJob,
    Invite,
    JobPosting,
    JobSnapshot,
    ProfileFact,
    Recommendation,
    SearchPlan,
    User,
)
from app.demo import data as demo_data
from app.demo.seed import run_demo_seed, seed_jobs_sync
from tests.integration.conftest import (
    MAILPIT_API,
    create_session_for,
    create_user,
    session_cookie,
)
from tests.integration.test_job_pipeline import run_source
from tests.integration.test_matching_pipeline import (
    create_facts,
    create_job,
    create_plan,
    run_matching,
)

VALID_IMPORT_TEXT = (
    "负责合成演示岗位的后端服务开发与维护，参与接口设计、性能优化与自动化测试建设。"
)


async def _canonical_by_posting_source_job_id(db_factory, source_job_id: str) -> CanonicalJob:
    async with db_factory() as db:
        posting = (
            await db.execute(
                select(JobPosting).where(JobPosting.source_job_id == source_job_id)
            )
        ).scalar_one()
        return (
            await db.execute(
                select(CanonicalJob).where(CanonicalJob.id == posting.canonical_job_id)
            )
        ).scalar_one()


# ---------------- data_origin 推导 ----------------


async def test_data_origin_connector_and_user_import(client, db_factory):
    """fixture 连接器 → connector；用户正文导入 → user_import。"""
    assert await run_source("fixture_a") == "success"
    async with db_factory() as db:
        rows = (
            (
                await db.execute(
                    select(CanonicalJob.data_origin).where(CanonicalJob.visibility == "global")
                )
            )
            .scalars()
            .all()
        )
    assert rows and all(v == "connector" for v in rows)

    user = await create_user(db_factory, "origin-import@cc-integration.dev")
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    resp = await client.post(
        "/api/v1/jobs/import",
        json={
            "title": "合成演示后端工程师",
            "company_name": "曦原合成科技有限公司",
            "city": "北京",
            "employment_text": "全职",
            "description_text": VALID_IMPORT_TEXT,
        },
    )
    assert resp.status_code == 201
    canonical_id = resp.json()["canonical_job_id"]
    async with db_factory() as db:
        canonical = (
            await db.execute(select(CanonicalJob).where(CanonicalJob.id == canonical_id))
        ).scalar_one()
    assert canonical.visibility == "private"
    assert canonical.data_origin == "user_import"


async def test_data_origin_synthetic_seed_via_seed_pipeline(db_factory):
    """种子管道产出的 canonical 全部 data_origin='synthetic_seed'（DB 层标注）。"""
    created, total = await asyncio.to_thread(seed_jobs_sync)
    assert created == total == len(demo_data.build_seed_job_payloads())
    async with db_factory() as db:
        origins = (
            (await db.execute(select(CanonicalJob.data_origin))).scalars().all()
        )
    assert len(origins) == total
    assert all(v == "synthetic_seed" for v in origins)


# ---------------- 推荐 API 透出 ----------------


async def test_recommendation_api_exposes_data_origin(client, db_factory):
    """列表与详情都返回 data_origin；synthetic_seed 时前端据此渲染「合成示例」徽标。"""
    user = await create_user(db_factory, "origin-rec@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    await create_job(db_factory, title="Python 后端开发工程师 普通")
    seeded_id = await create_job(db_factory, title="Python 后端开发工程师 种子")
    async with db_factory() as db:
        canonical = (
            await db.execute(select(CanonicalJob).where(CanonicalJob.id == seeded_id))
        ).scalar_one()
        canonical.data_origin = "synthetic_seed"
        await db.commit()
    run_matching(plan.id)
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))

    items = (await client.get("/api/v1/recommendations")).json()["items"]
    assert len(items) == 2
    by_job = {item["job_title"]: item["data_origin"] for item in items}
    assert by_job["Python 后端开发工程师 种子"] == "synthetic_seed"
    assert by_job["Python 后端开发工程师 普通"] == "user_import"

    for item in items:
        detail = (await client.get(f"/api/v1/recommendations/{item['id']}")).json()
        assert detail["data_origin"] == item["data_origin"]


# ---------------- seed 幂等 ----------------


async def _table_counts(db_factory) -> dict[str, int]:
    async with db_factory() as db:
        counts = {}
        for name, model in (
            ("users", User),
            ("invites", Invite),
            ("profile_facts", ProfileFact),
            ("canonical_jobs", CanonicalJob),
            ("job_postings", JobPosting),
            ("job_snapshots", JobSnapshot),
            ("search_plans", SearchPlan),
            ("recommendations", Recommendation),
        ):
            counts[name] = int(
                (await db.execute(select(func.count()).select_from(model))).scalar_one()
            )
        return counts


async def test_seed_jobs_sync_idempotent(db_factory):
    """重复执行零新增：岗位、posting、快照都不膨胀。"""
    created_1, total = await asyncio.to_thread(seed_jobs_sync)
    counts_1 = await _table_counts(db_factory)
    created_2, _ = await asyncio.to_thread(seed_jobs_sync)
    counts_2 = await _table_counts(db_factory)
    assert created_1 == total and created_2 == 0
    assert counts_1 == counts_2


async def test_run_demo_seed_full_flow_and_idempotent(db_factory, real_env):
    """全流程：正常注册（邀请码+Mailpit）→ 事实库 → 种子岗位 → 方案 → 实跑推荐。

    - 注册确实消耗了一次性注册邀请码（无后门证据：invites.used_count=1）；
    - 推荐非空（demo 用户登录即有内容可看）；
    - 重复执行完全幂等（所有相关表行数不变）。
    """
    report_1 = await run_demo_seed(mailpit_api=MAILPIT_API, mail_timeout_seconds=30)
    assert report_1.user_created is True
    assert report_1.active_facts >= 3
    assert report_1.seed_jobs_created == report_1.seed_jobs_total
    assert report_1.plan_created is True
    assert report_1.recommendations_total > 0

    async with db_factory() as db:
        user = (
            await db.execute(
                select(User).where(User.email_normalized == demo_data.DEMO_EMAIL)
            )
        ).scalar_one()
        assert user.age_attested_at is not None  # 正常注册流程写入
        used = (
            (await db.execute(select(Invite.used_count))).scalars().all()
        )
        assert sorted(used) == [0, 1]  # 注册码已消耗 1 次；登录码未消耗

        seed_origins = (
            (
                await db.execute(
                    select(CanonicalJob.data_origin).where(
                        CanonicalJob.visibility == "global"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(seed_origins) == report_1.seed_jobs_total
        assert all(v == "synthetic_seed" for v in seed_origins)

    counts_1 = await _table_counts(db_factory)
    report_2 = await run_demo_seed(mailpit_api=MAILPIT_API, mail_timeout_seconds=30)
    counts_2 = await _table_counts(db_factory)
    assert report_2.user_created is False
    assert report_2.seed_jobs_created == 0
    assert report_2.plan_created is False
    assert report_2.recommendations_total == report_1.recommendations_total
    assert counts_1 == counts_2
