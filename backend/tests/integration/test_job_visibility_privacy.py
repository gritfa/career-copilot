"""阶段 10 任务 B：用户导入岗位的归属与可见性（跨用户泄露修复）集成测试。

产品规则（真实 PG/pgvector + Redis）：
- 连接器公开岗位 → global，进所有用户候选池；
- 用户导入 → private + owner，只推荐给导入者本人；
- 他人私有岗位在所有岗位读取接口一律 404（不泄露存在性）；
- 注销硬删 → 个人岗位（canonical/posting/快照行+存储对象/向量）物理清理；
- 个人岗位与公开岗位重复时只记"可能相同"，个人快照/来源绝不跨用户暴露。
"""

import asyncio
import uuid

from sqlalchemy import select, update

from tests.integration.conftest import (
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)
from tests.integration.test_matching_pipeline import (
    create_facts,
    create_job,
    create_plan,
    fetch_recommendations,
    run_matching,
)

IMPORT_PAYLOAD = {
    "title": "Python 后端开发工程师",
    "company_name": "私有导入小厂",
    "city": "北京",
    "salary_text": "20-35K",
    "experience_text": "1-3年",
    "education_text": "本科及以上",
    "description_text": "负责 FastAPI 服务开发，PostgreSQL 与 Redis 调优，参与稳定性建设。",
}


async def _login_new_user(make_client, db_factory, prefix: str):
    """独立 cookie jar 的已登录用户（含已确认事实 + active 方案）。"""
    user = await create_user(db_factory, unique_email(prefix))
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    client = make_client()
    client.cookies.set(session_cookie(), await create_session_for(db_factory, user))
    return user, plan, client


async def _import_job(client, payload=None) -> dict:
    resp = await client.post("/api/v1/jobs/import", json=payload or IMPORT_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _purge_account(db_factory, user_id) -> dict:
    """把账号置为 deletion_pending 并同步执行硬删。"""
    from app.db.models import User
    from app.privacy.service import execute_account_purge
    from tests.integration.test_account_purge import _sync_session

    async with db_factory() as db:
        await db.execute(
            update(User).where(User.id == user_id).values(status="deletion_pending")
        )
        await db.commit()

    def _run() -> dict:
        db_sync, engine = _sync_session()
        try:
            return execute_account_purge(db_sync, user_id)
        finally:
            db_sync.close()
            engine.dispose()

    return await asyncio.to_thread(_run)


# ---------------- 1. A 导入 → 推荐给 A ----------------


async def test_imported_job_recommended_to_importer(db_factory, make_client):
    user_a, plan_a, client_a = await _login_new_user(make_client, db_factory, "vis-a1")
    body = await _import_job(client_a)
    canonical_id = uuid.UUID(body["canonical_job_id"])

    from app.db.models import CanonicalJob

    async with db_factory() as db:
        canonical = (
            await db.execute(select(CanonicalJob).where(CanonicalJob.id == canonical_id))
        ).scalar_one()
    assert canonical.visibility == "private"
    assert canonical.owner_user_id == user_a.id

    stats = run_matching(plan_a.id)
    assert stats["created"] == 1
    recs = await fetch_recommendations(db_factory, plan_a.id)
    assert [r.canonical_job_id for r in recs] == [canonical_id]


# ---------------- 2. A 导入 → 绝不推荐给 B ----------------


async def test_imported_job_never_recommended_to_other_user(db_factory, make_client):
    _user_a, plan_a, client_a = await _login_new_user(make_client, db_factory, "vis-a2")
    _user_b, plan_b, _client_b = await _login_new_user(make_client, db_factory, "vis-b2")
    body = await _import_job(client_a)
    canonical_id = uuid.UUID(body["canonical_job_id"])

    assert run_matching(plan_a.id)["created"] == 1
    stats_b = run_matching(plan_b.id)
    # B 的候选池根本不含 A 的私有岗位（不是评分后被过滤，而是压根不进池）
    assert stats_b["evaluated"] == 0
    assert stats_b["created"] == 0
    recs_b = await fetch_recommendations(db_factory, plan_b.id)
    assert canonical_id not in {r.canonical_job_id for r in recs_b}


# ---------------- 3. B 猜 ID：他人私有岗位一律 404 ----------------


async def test_other_user_private_job_reads_are_404(db_factory, make_client):
    _user_a, _plan_a, client_a = await _login_new_user(make_client, db_factory, "vis-a3")
    _user_b, _plan_b, client_b = await _login_new_user(make_client, db_factory, "vis-b3")
    body = await _import_job(client_a)
    canonical_id = body["canonical_job_id"]

    # 导入者本人可读来源
    r_owner = await client_a.get(f"/api/v1/jobs/{canonical_id}/sources")
    assert r_owner.status_code == 200
    assert [i["source_key"] for i in r_owner.json()["items"]] == ["user_import"]

    # 他人：来源 / 有效性检查一律 404（与不存在无差别，正文与来源零暴露）
    r_sources = await client_b.get(f"/api/v1/jobs/{canonical_id}/sources")
    assert r_sources.status_code == 404
    assert "user_import" not in r_sources.text
    assert IMPORT_PAYLOAD["description_text"] not in r_sources.text
    r_validity = await client_b.post(f"/api/v1/jobs/{canonical_id}/validity-check")
    assert r_validity.status_code == 404


# ---------------- 4. 公开岗位对 A 和 B 都推荐 ----------------


async def test_global_job_recommended_to_both_users(db_factory, make_client):
    _user_a, plan_a, client_a = await _login_new_user(make_client, db_factory, "vis-a4")
    _user_b, plan_b, client_b = await _login_new_user(make_client, db_factory, "vis-b4")
    global_id = await create_job(db_factory)  # 连接器（company_site）→ 默认 global

    from app.db.models import CanonicalJob

    async with db_factory() as db:
        canonical = (
            await db.execute(select(CanonicalJob).where(CanonicalJob.id == global_id))
        ).scalar_one()
    assert canonical.visibility == "global" and canonical.owner_user_id is None

    assert run_matching(plan_a.id)["created"] == 1
    assert run_matching(plan_b.id)["created"] == 1
    recs_a = await fetch_recommendations(db_factory, plan_a.id)
    recs_b = await fetch_recommendations(db_factory, plan_b.id)
    assert {r.canonical_job_id for r in recs_a} == {global_id}
    assert {r.canonical_job_id for r in recs_b} == {global_id}
    # 公开岗位的读取接口对两人都可用
    assert (await client_a.get(f"/api/v1/jobs/{global_id}/sources")).status_code == 200
    assert (await client_b.get(f"/api/v1/jobs/{global_id}/sources")).status_code == 200


# ---------------- 5. 注销硬删：个人岗位不残留任何候选池 ----------------


async def test_purge_removes_private_jobs_everywhere(db_factory, make_client):
    user_a, plan_a, client_a = await _login_new_user(make_client, db_factory, "vis-a5")
    _user_b, plan_b, _client_b = await _login_new_user(make_client, db_factory, "vis-b5")
    body = await _import_job(client_a)
    canonical_id = uuid.UUID(body["canonical_job_id"])
    posting_id = uuid.UUID(body["posting_id"])
    assert run_matching(plan_a.id)["created"] == 1  # 生成向量/推荐后再删，覆盖级联

    from app.db.models import CanonicalJob, JobPosting, JobSnapshot, JobVector
    from app.integrations.storage import get_storage

    async with db_factory() as db:
        posting = (
            await db.execute(select(JobPosting).where(JobPosting.id == posting_id))
        ).scalar_one()
        snapshot = (
            await db.execute(select(JobSnapshot).where(JobSnapshot.id == posting.snapshot_id))
        ).scalar_one()
    snapshot_key = snapshot.storage_key
    assert get_storage().exists(snapshot_key)

    outcome = await _purge_account(db_factory, user_a.id)
    assert outcome["status"] == "succeeded", outcome
    manifest = outcome["manifest"]
    assert manifest["imported_postings"] == 1
    assert manifest["private_canonical_jobs"] == 1
    assert manifest["job_snapshots_purged"] == 1

    # DB 行 / 向量 / 存储对象全部物理清理
    async with db_factory() as db:
        assert (
            await db.execute(select(CanonicalJob).where(CanonicalJob.id == canonical_id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(JobPosting).where(JobPosting.id == posting_id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(select(JobSnapshot).where(JobSnapshot.id == snapshot.id))
        ).scalar_one_or_none() is None
        assert (
            await db.execute(
                select(JobVector).where(JobVector.canonical_job_id == canonical_id)
            )
        ).scalar_one_or_none() is None
    assert not get_storage().exists(snapshot_key)

    # 任何人的候选池都不再出现该岗位
    stats_b = run_matching(plan_b.id)
    assert stats_b["evaluated"] == 0 and stats_b["created"] == 0


# ---------------- 6. 个人与公开岗位重复：个人快照/来源不泄露 ----------------


async def test_duplicate_private_job_does_not_leak_into_global(db_factory, make_client):
    _user_a, plan_a, client_a = await _login_new_user(make_client, db_factory, "vis-a6")
    _user_b, plan_b, client_b = await _login_new_user(make_client, db_factory, "vis-b6")

    private_marker = "个人快照专属标记JOBCANARY壹贰叁"
    shared_desc = "负责 FastAPI 服务开发，PostgreSQL 与 Redis 调优，参与稳定性建设。"
    global_id = await create_job(
        db_factory,
        description=shared_desc,
        company_name="重复检测科技",
    )
    body = await _import_job(
        client_a,
        {
            **IMPORT_PAYLOAD,
            "company_name": "重复检测科技",
            "description_text": f"{shared_desc}{private_marker}",
        },
    )
    private_id = uuid.UUID(body["canonical_job_id"])
    assert private_id != global_id  # 允许识别"可能相同"，但绝不合并

    from app.db.models import CanonicalJob, JobPosting

    async with db_factory() as db:
        private_canonical = (
            await db.execute(select(CanonicalJob).where(CanonicalJob.id == private_id))
        ).scalar_one()
        own_posting = (
            await db.execute(
                select(JobPosting).where(JobPosting.id == uuid.UUID(body["posting_id"]))
            )
        ).scalar_one()
    assert private_canonical.visibility == "private"
    assert own_posting.dedupe_candidate_canonical_id == global_id  # "可能相同"已识别

    # B 视角：公开岗位来源链接无 user_import、无个人正文
    r_sources = await client_b.get(f"/api/v1/jobs/{global_id}/sources")
    assert r_sources.status_code == 200
    assert all(i["source_key"] != "user_import" for i in r_sources.json()["items"])
    assert private_marker not in r_sources.text

    # B 视角：推荐详情（正文/来源链接）不含 A 的个人快照内容
    assert run_matching(plan_b.id)["created"] == 1
    recs_b = await fetch_recommendations(db_factory, plan_b.id)
    assert {r.canonical_job_id for r in recs_b} == {global_id}
    detail = await client_b.get(f"/api/v1/recommendations/{recs_b[0].id}")
    assert detail.status_code == 200
    assert private_marker not in detail.text
    assert all(
        link["source_key"] != "user_import" for link in detail.json()["source_links"]
    )

    # A 视角：自己同时拿到两条（公开 + 本人私有），互不影响
    stats_a = run_matching(plan_a.id)
    assert stats_a["created"] == 2
    recs_a = await fetch_recommendations(db_factory, plan_a.id)
    assert {r.canonical_job_id for r in recs_a} == {global_id, private_id}
