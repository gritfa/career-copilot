"""岗位来源全管道集成测试（真实 PG/Redis/存储）。

覆盖：fixture 连接器 E2E（discover→snapshot→normalize→dedupe→canonical）、
快照不可变、标准化各分支落库、去重三层、用户导入（正文/URL）、
有效性三态、来源链接保留、审计不含正文。
"""

import asyncio

import psycopg
import pytest
from sqlalchemy import select

from app.db.models import (
    AuditEvent,
    CanonicalJob,
    Company,
    JobPosting,
    JobSnapshot,
    JobSource,
    SourceRun,
)
from app.jobs.dedupe import HIGH_CONFIDENCE, MID_CONFIDENCE, text_similarity
from app.jobs.tasks import sync_source_task
from tests.integration.conftest import (
    TEST_SYNC_DSN,
    create_session_for,
    create_user,
    session_cookie,
    unique_email,
)

# fixture_a XL-002 的原文（用于构造跨源高/中置信导入样本）
XL002_TITLE = "Python 后端开发工程师"
XL002_COMPANY = "星岚科技"
XL002_CITY = "杭州"
XL002_DESC = (
    "负责核心交易服务的 FastAPI/异步微服务开发，PostgreSQL 与 Redis 调优，"
    "参与服务可观测性与稳定性建设。"
)


async def login(client, db_factory):
    user = await create_user(db_factory, unique_email())
    cookie = await create_session_for(db_factory, user)
    client.cookies.set(session_cookie(), cookie)
    return user


async def run_source(source_key: str, run_key: str | None = None):
    """在无事件循环的线程里执行任务（任务内部使用 asyncio.run 驱动 Adapter）。"""
    return await asyncio.to_thread(
        lambda: sync_source_task.apply(args=(source_key, run_key)).result
    )


async def get_posting(db_factory, source_job_id: str) -> JobPosting:
    async with db_factory() as db:
        return (
            await db.execute(
                select(JobPosting).where(JobPosting.source_job_id == source_job_id)
            )
        ).scalar_one()


# ---------------- fixture 连接器 E2E ----------------


async def test_fixture_a_full_pipeline_e2e(client, db_factory):
    status = await run_source("fixture_a")
    assert status == "success"

    async with db_factory() as db:
        source = (
            await db.execute(select(JobSource).where(JobSource.source_key == "fixture_a"))
        ).scalar_one()
        run = (
            await db.execute(select(SourceRun).where(SourceRun.job_source_id == source.id))
        ).scalar_one()
        postings = (
            (await db.execute(select(JobPosting).where(JobPosting.job_source_id == source.id)))
            .scalars()
            .all()
        )
        snapshots = (
            (
                await db.execute(
                    select(JobSnapshot).where(JobSnapshot.job_source_id == source.id)
                )
            )
            .scalars()
            .all()
        )
        canonicals = (await db.execute(select(CanonicalJob))).scalars().all()
        company = (
            await db.execute(select(Company).where(Company.canonical_name == "星岚科技"))
        ).scalar_one()

    # run 统计
    assert run.status == "success"
    assert run.items_seen == 10 and run.items_new == 10 and run.items_failed == 0
    assert run.completed_at is not None
    # 快照：每条详情一条不可变证据，原始内容在对象存储
    assert len(snapshots) == 10
    from app.integrations.storage import get_storage

    storage = get_storage()
    for snap in snapshots:
        assert snap.content_hash and len(snap.content_hash) == 64
        assert storage.exists(snap.storage_key)
    # posting 全部入库；实习岗（XL-009 非全职）不进 canonical 池
    assert len(postings) == 10
    intern = next(p for p in postings if p.source_job_id == "XL-009")
    assert intern.employment_type == "other" and intern.canonical_job_id is None
    assert len(canonicals) == 9
    # 公司主数据建立
    assert company is not None


async def test_fixture_pipeline_normalization_branches(client, db_factory):
    await run_source("fixture_a")

    # 13/14 薪
    p2 = await get_posting(db_factory, "XL-002")
    assert (p2.salary_min, p2.salary_max, p2.salary_months) == (20000, 35000, 13)
    assert p2.role_family == "backend_python"
    assert p2.city_code == "330100" and p2.city_kind == "city"
    p1 = await get_posting(db_factory, "XL-001")
    assert p1.salary_months == 14 and p1.role_family == "ai_application"

    # 面议：不伪造数值 + 学历 preferred
    p6 = await get_posting(db_factory, "XL-006")
    assert p6.salary_unknown is True
    assert p6.salary_min is None and p6.salary_max is None
    assert p6.education_requirement_type == "preferred"
    assert p6.experience_type == "unrestricted"

    # 学历 required
    assert p2.education_requirement_type == "required"
    assert p2.education_level == "bachelor"

    # 城市无法映射（武汉）→ other，不硬塞六城代码；年薪换算带置信度
    p8 = await get_posting(db_factory, "XL-008")
    assert p8.city_code is None and p8.city_kind == "other"
    assert p8.salary_confidence is not None and p8.salary_confidence < 1.0
    assert p8.salary_raw == "20-35万/年"

    # 日薪换算（实习岗）
    p9 = await get_posting(db_factory, "XL-009")
    assert p9.salary_confidence is not None and p9.salary_confidence < 1.0
    assert p9.salary_min == int(300 * 21.75)

    # 外包/派遣/驻场信号：带证据与置信度
    p7 = await get_posting(db_factory, "XL-007")
    assert p7.outsourcing_signals, "外包岗位必须有风险信号"
    assert all(
        s["code"] == "POSSIBLE_OUTSOURCING" and s["evidence"] and 0 < s["confidence"] <= 1
        for s in p7.outsourcing_signals
    )
    # 干净岗位无信号
    assert p1.outsourcing_signals == []


async def test_source_run_idempotent_and_incremental(client, db_factory):
    assert await run_source("fixture_a", "20260729") == "success"
    # 同 run_key 重复投递：幂等跳过
    assert await run_source("fixture_a", "20260729") == "already_ran"
    # 新 run_key（次日增量）：同源确定性去重 → 无新增 posting
    assert await run_source("fixture_a", "20260730") == "success"
    async with db_factory() as db:
        source = (
            await db.execute(select(JobSource).where(JobSource.source_key == "fixture_a"))
        ).scalar_one()
        run2 = (
            await db.execute(
                select(SourceRun).where(
                    SourceRun.job_source_id == source.id, SourceRun.run_key == "20260730"
                )
            )
        ).scalar_one()
        postings = (
            (await db.execute(select(JobPosting).where(JobPosting.job_source_id == source.id)))
            .scalars()
            .all()
        )
    assert run2.items_seen == 10 and run2.items_new == 0 and run2.items_failed == 0
    assert len(postings) == 10, "同源 ID 确定性重复不得生成新 posting"


# ---------------- 快照不可变 ----------------


async def test_job_snapshots_immutable_at_db_level(client, db_factory):
    await run_source("fixture_a")
    with psycopg.connect(TEST_SYNC_DSN, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("UPDATE job_snapshots SET content_hash = repeat('0', 64)")
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM job_snapshots")


async def test_source_runs_success_with_failures_rejected_by_db(client, db_factory):
    """DB CHECK 兜底：有失败条目的 run 不可能被写成 success。"""
    await run_source("fixture_a")
    with psycopg.connect(TEST_SYNC_DSN, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            conn.execute(
                "UPDATE source_runs SET items_failed = 1 WHERE status = 'success'"
            )


# ---------------- 去重三层 ----------------


async def test_dedupe_cross_source_high_confidence_merges(client, db_factory):
    """跨源同岗（公司+职位+城市键相同、正文高相似）→ 自动合并，官网为主来源。"""
    await run_source("fixture_a")
    await login(client, db_factory)

    ratio = text_similarity(XL002_DESC, XL002_DESC)
    assert ratio is not None and ratio >= HIGH_CONFIDENCE

    resp = await client.post(
        "/api/v1/jobs/import",
        json={
            "title": XL002_TITLE,
            "company_name": XL002_COMPANY,
            "city": XL002_CITY,
            "salary_text": "20-35K·13薪",
            "description_text": XL002_DESC,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dedupe_status"] == "merged"

    fixture_posting = await get_posting(db_factory, "XL-002")
    assert body["canonical_job_id"] == str(fixture_posting.canonical_job_id)

    # 全部来源链接保留；企业官网是主来源
    sources = await client.get(f"/api/v1/jobs/{body['canonical_job_id']}/sources")
    assert sources.status_code == 200
    items = sources.json()["items"]
    assert len(items) == 2
    by_key = {i["source_key"]: i for i in items}
    assert by_key["fixture_a"]["is_primary"] is True
    assert by_key["user_import"]["is_primary"] is False


async def test_dedupe_mid_confidence_goes_to_review_queue(client, db_factory):
    await run_source("fixture_a")
    await login(client, db_factory)

    similar_desc = (
        "负责核心交易服务的 FastAPI/异步微服务开发，PostgreSQL 与 Redis 调优，"
        "主导跨团队架构评审与代码规范建设专项工作。"
    )
    ratio = text_similarity(XL002_DESC, similar_desc)
    assert ratio is not None and MID_CONFIDENCE <= ratio < HIGH_CONFIDENCE, ratio

    resp = await client.post(
        "/api/v1/jobs/import",
        json={
            "title": XL002_TITLE,
            "company_name": XL002_COMPANY,
            "city": XL002_CITY,
            "description_text": similar_desc,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dedupe_status"] == "pending_review"
    assert body["canonical_job_id"] is None  # 待审期间不并池

    async with db_factory() as db:
        posting = (
            await db.execute(
                select(JobPosting).where(JobPosting.id == body["posting_id"])
            )
        ).scalar_one()
        fixture_posting = (
            await db.execute(select(JobPosting).where(JobPosting.source_job_id == "XL-002"))
        ).scalar_one()
    assert posting.dedupe_candidate_canonical_id == fixture_posting.canonical_job_id


async def test_dedupe_different_jobs_not_merged(client, db_factory):
    await run_source("fixture_a")
    await run_source("fixture_b")
    await login(client, db_factory)

    # 不同公司同名职位（XL-002 vs YT-106，城市也不同）不得合并
    async with db_factory() as db:
        p_a = (
            await db.execute(select(JobPosting).where(JobPosting.source_job_id == "XL-002"))
        ).scalar_one()
        p_b = (
            await db.execute(select(JobPosting).where(JobPosting.source_job_id == "YT-106"))
        ).scalar_one()
    assert p_a.canonical_job_id != p_b.canonical_job_id

    # 全新岗位导入 → 新 canonical
    resp = await client.post(
        "/api/v1/jobs/import",
        json={
            "title": "Golang 后端开发工程师",
            "company_name": "完全不同的公司",
            "city": "北京",
            "description_text": "负责区块链节点服务开发，Go 语言，分布式共识协议实现与优化。",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["dedupe_status"] == "unique"
    assert resp.json()["canonical_job_id"] is not None


# ---------------- 用户导入 ----------------


async def test_import_text_creates_canonical_and_idempotent(client, db_factory):
    await login(client, db_factory)
    payload = {
        "title": "Java 后端开发工程师",
        "company_name": "口碑小厂",
        "city": "广州",
        "salary_text": "18-28K·13薪",
        "experience_text": "1-3年",
        "education_text": "本科及以上",
        "description_text": "负责电商订单 Java 微服务开发，Spring Boot，MySQL 分库分表实践。",
    }
    r1 = await client.post("/api/v1/jobs/import", json=payload)
    assert r1.status_code == 201, r1.text
    assert r1.json()["created"] is True
    assert r1.json()["canonical_job_id"] is not None

    # 相同正文重复导入：同源确定性去重（content hash 作 source_job_id）
    r2 = await client.post("/api/v1/jobs/import", json=payload)
    assert r2.status_code == 201
    assert r2.json()["created"] is False
    assert r2.json()["posting_id"] == r1.json()["posting_id"]

    async with db_factory() as db:
        posting = (
            await db.execute(
                select(JobPosting).where(JobPosting.id == r1.json()["posting_id"])
            )
        ).scalar_one()
    assert posting.salary_min == 18000 and posting.salary_months == 13
    assert posting.role_family == "backend_java"
    assert posting.snapshot_id is not None, "正文导入必须有不可变快照"


async def test_import_url_stores_reference_without_fetch(client, db_factory):
    await login(client, db_factory)
    url = "https://www.zhipin.com/job_detail/abc123.html"
    r1 = await client.post("/api/v1/jobs/import", json={"url": url})
    assert r1.status_code == 201, r1.text
    body = r1.json()
    assert body["imported_via"] == "url"
    assert body["canonical_job_id"] is None

    async with db_factory() as db:
        posting = (
            await db.execute(select(JobPosting).where(JobPosting.id == body["posting_id"]))
        ).scalar_one()
        snapshots = (await db.execute(select(JobSnapshot))).scalars().all()
    # 只存引用：无快照、无正文、状态 unknown、薪资 unknown
    assert posting.source_url == url
    assert posting.snapshot_id is None and snapshots == []
    assert posting.description_text is None
    assert posting.status == "unknown" and posting.salary_unknown is True

    # 同 URL 重复导入幂等
    r2 = await client.post("/api/v1/jobs/import", json={"url": url})
    assert r2.json()["created"] is False

    # URL 和正文都缺失 → 422
    assert (await client.post("/api/v1/jobs/import", json={})).status_code == 422


async def test_import_requires_active_login(client):
    resp = await client.post(
        "/api/v1/jobs/import", json={"url": "https://example.com/job/1"}
    )
    assert resp.status_code == 401


# ---------------- 有效性三态 ----------------


async def test_validity_check_three_states(client, db_factory):
    await run_source("fixture_a")
    await login(client, db_factory)

    # fixture 标记下架的岗位 → inactive
    p10 = await get_posting(db_factory, "XL-010")
    r_inactive = await client.post(f"/api/v1/jobs/{p10.canonical_job_id}/validity-check")
    assert r_inactive.status_code == 200, r_inactive.text
    assert r_inactive.json()["status"] == "inactive"

    # 在招岗位 → active
    p1 = await get_posting(db_factory, "XL-001")
    r_active = await client.post(f"/api/v1/jobs/{p1.canonical_job_id}/validity-check")
    assert r_active.json()["status"] == "active"

    # 用户导入正文（无自动访问方式）→ unknown，不伪造结论
    imported = await client.post(
        "/api/v1/jobs/import",
        json={
            "title": "前端开发工程师",
            "company_name": "无名公司",
            "city": "上海",
            "description_text": "负责营销页面前端开发，Vue3 + Vite，组件抽象与性能优化。",
        },
    )
    r_unknown = await client.post(
        f"/api/v1/jobs/{imported.json()['canonical_job_id']}/validity-check"
    )
    assert r_unknown.json()["status"] == "unknown"
    assert r_unknown.json()["reason"] == "NO_AUTOMATED_ACCESS"

    # 状态回写到 posting / canonical
    async with db_factory() as db:
        p10_after = (
            await db.execute(select(JobPosting).where(JobPosting.source_job_id == "XL-010"))
        ).scalar_one()
        c10 = (
            await db.execute(
                select(CanonicalJob).where(CanonicalJob.id == p10_after.canonical_job_id)
            )
        ).scalar_one()
    assert p10_after.status == "inactive" and c10.status == "inactive"


# ---------------- 审计不含正文 ----------------


async def test_import_audit_contains_no_job_body(client, db_factory):
    await login(client, db_factory)
    secret_desc = "绝密正文标记XYZZY：负责某核心系统开发，要求熟悉内部框架细节。"
    resp = await client.post(
        "/api/v1/jobs/import",
        json={
            "title": "标记岗位",
            "company_name": "审计测试公司",
            "description_text": secret_desc,
        },
    )
    assert resp.status_code == 201

    async with db_factory() as db:
        events = (await db.execute(select(AuditEvent))).scalars().all()
    assert any(e.action == "job_imported" for e in events)
    for event in events:
        joined = " ".join(
            str(v)
            for v in (
                event.action,
                event.resource_type,
                event.resource_id,
                event.reason_code,
            )
            if v
        )
        assert "XYZZY" not in joined, "审计不得包含岗位正文"
        assert "绝密" not in joined
