"""匹配管道集成测试（真实 PostgreSQL + pgvector + Redis）。

覆盖：硬过滤三态入库、salary_unknown 不误放不误杀、屏蔽公司、
每日 20 上限、同日唯一约束、历史不重复推荐、日志无正文。
"""

import uuid
from datetime import UTC, datetime

import structlog.testing

from tests.integration.conftest import create_user

NORMALIZER_VERSION = "1"


async def create_plan(db_factory, user, **overrides):
    from app.db.models import SearchPlan

    defaults = dict(
        user_id=user.id,
        name="后端方案",
        role_family="backend_python",
        status="active",
        priority=0,
        city_codes=["110100"],
        work_modes=["onsite", "hybrid"],
        minimum_monthly_salary=None,
        target_monthly_salary=None,
        minimum_match_score=0,
        allow_outsourcing=False,
    )
    defaults.update(overrides)
    async with db_factory() as db:
        plan = SearchPlan(**defaults)
        db.add(plan)
        await db.commit()
        await db.refresh(plan)
        return plan


async def create_facts(db_factory, user):
    """已确认事实：Python/FastAPI 技能 + 3 年经验 + 本科。"""
    from app.db.models import ProfileFact

    now = datetime.now(UTC)
    rows = [
        ProfileFact(
            user_id=user.id,
            fact_type="skill",
            value_json={"name": "Python", "detail": "FastAPI、asyncio、PostgreSQL、Redis"},
            status="active",
            provenance_type="resume",
            confirmed_by_user_at=now,
        ),
        ProfileFact(
            user_id=user.id,
            fact_type="work_experience",
            value_json={
                "company": "星辰科技有限公司",
                "start_raw": "2023.7",
                "end_raw": "至今",
                "is_current": True,
            },
            status="active",
            provenance_type="resume",
            confirmed_by_user_at=now,
        ),
        ProfileFact(
            user_id=user.id,
            fact_type="education",
            value_json={"school": "郑州大学", "degree": "本科"},
            status="active",
            provenance_type="resume",
            confirmed_by_user_at=now,
        ),
    ]
    async with db_factory() as db:
        db.add_all(rows)
        await db.commit()
    return rows


_source_cache: dict[str, uuid.UUID] = {}


async def _get_source_id(db_factory) -> uuid.UUID:
    from sqlalchemy import select

    from app.db.models import JobSource

    async with db_factory() as db:
        source = (
            await db.execute(select(JobSource).where(JobSource.source_key == "test_matching"))
        ).scalar_one_or_none()
        if source is None:
            source = JobSource(
                source_key="test_matching",
                name="匹配测试来源",
                source_type="company_site",
                status="enabled",
            )
            db.add(source)
            await db.commit()
            await db.refresh(source)
        return source.id


async def create_job(
    db_factory,
    *,
    title="Python 后端开发工程师",
    description="负责 FastAPI 服务开发，PostgreSQL 与 Redis 调优。",
    role_family="backend_python",
    city_code="110100",
    salary=(25000, 40000),
    salary_unknown=False,
    experience=(1, 3),
    education=("bachelor", "required"),
    employment_type="full_time",
    company_name=None,
    outsourcing_signals=None,
):
    """直接建 canonical_job + 主 posting（绕过采集管道，聚焦匹配逻辑）。"""
    from app.db.models import CanonicalJob, Company, JobPosting
    from app.jobs.normalize import normalize_title

    source_id = await _get_source_id(db_factory)
    async with db_factory() as db:
        company_id = None
        if company_name:
            from sqlalchemy import select

            company = (
                await db.execute(select(Company).where(Company.canonical_name == company_name))
            ).scalar_one_or_none()
            if company is None:
                company = Company(canonical_name=company_name, aliases=[], source_refs_json=[])
                db.add(company)
                await db.flush()
            company_id = company.id
        canonical = CanonicalJob(
            title_normalized=normalize_title(title),
            role_family=role_family,
            company_id=company_id,
            city_code=city_code,
            dedupe_version="1",
            status="active",
        )
        db.add(canonical)
        await db.flush()
        posting = JobPosting(
            job_source_id=source_id,
            source_job_id=f"tm-{uuid.uuid4().hex[:10]}",
            canonical_job_id=canonical.id,
            company_id=company_id,
            title_raw=title,
            title_normalized=normalize_title(title),
            role_family=role_family,
            description_text=description,
            city_code=city_code,
            city_kind="city" if city_code else "unknown",
            employment_type=employment_type,
            salary_min=None if salary_unknown else salary[0],
            salary_max=None if salary_unknown else salary[1],
            salary_unknown=salary_unknown,
            salary_raw="面议" if salary_unknown else f"{salary[0]}-{salary[1]}",
            experience_min=experience[0] if experience else None,
            experience_max=experience[1] if experience else None,
            experience_type="range" if experience else "unrestricted",
            education_level=education[0] if education else None,
            education_requirement_type=education[1] if education else "unknown",
            outsourcing_signals=outsourcing_signals or [],
            source_url=f"https://jobs.example-fixture.dev/{uuid.uuid4().hex[:8]}",
            normalizer_version=NORMALIZER_VERSION,
        )
        db.add(posting)
        await db.flush()
        canonical.primary_posting_id = posting.id
        await db.commit()
        return canonical.id


def run_matching(plan_id, run_date=None):
    from app.matching.tasks import generate_recommendations_task

    args = (str(plan_id),) if run_date is None else (str(plan_id), run_date)
    return generate_recommendations_task.apply(args=args).get()


async def fetch_recommendations(db_factory, plan_id):
    from sqlalchemy import select

    from app.db.models import Recommendation

    async with db_factory() as db:
        return (
            (
                await db.execute(
                    select(Recommendation).where(Recommendation.search_plan_id == plan_id)
                )
            )
            .scalars()
            .all()
        )


# ---------------- 端到端：硬过滤 → 召回 → 评分 → 入库 ----------------


async def test_generate_recommendations_end_to_end(db_factory):
    user = await create_user(db_factory, "match-e2e@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    good = await create_job(db_factory)
    # 城市不符 → 硬过滤 failed，绝不入库
    await create_job(db_factory, city_code="310100", title="Python 开发（上海）")

    stats = run_matching(plan.id)
    assert stats["status"] == "ok"
    assert stats["created"] == 1

    recs = await fetch_recommendations(db_factory, plan.id)
    assert len(recs) == 1
    rec = recs[0]
    assert rec.canonical_job_id == good
    assert rec.hard_filter_status == "passed"
    assert rec.scoring_version == "score_v1"
    assert rec.versions_json["hard_rule_version"] == "hard_v1"
    assert rec.versions_json["embedding_model_id"] == "det-hash-768@1"
    # 硬条件明细：8 项全部带三态状态和证据
    assert len(rec.hard_conditions_json) == 8
    for item in rec.hard_conditions_json:
        assert item["status"] in ("passed", "passed_with_penalty", "failed", "unknown")
        assert item["evidence"]

    # 分项：6 项都有证据或显式 insufficient_evidence
    from sqlalchemy import select

    from app.db.models import MatchComponent

    async with db_factory() as db:
        comps = (
            (
                await db.execute(
                    select(MatchComponent).where(MatchComponent.recommendation_id == rec.id)
                )
            )
            .scalars()
            .all()
        )
    assert {c.component for c in comps} == {
        "core_skills",
        "experience",
        "project_evidence",
        "role_semantic",
        "industry",
        "preference",
    }
    for comp in comps:
        assert comp.evidence_refs_json or comp.uncertainty == "insufficient_evidence"

    # 费用账本：确定性 Adapter 也要记账（零费用）
    from app.db.models import UsageLedger

    async with db_factory() as db:
        ledger = (await db.execute(select(UsageLedger))).scalars().all()
    assert ledger, "embedding 调用必须写 usage_ledger"
    assert all(row.operation_type == "embedding" for row in ledger)


async def test_salary_unknown_goes_to_uncertain_not_dropped(db_factory):
    """薪资面议 + 方案有最低薪资：不误杀（入库）也不误放（uncertain）。"""
    user = await create_user(db_factory, "match-salary@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user, minimum_monthly_salary=20000)
    job = await create_job(db_factory, salary_unknown=True, salary=None)

    run_matching(plan.id)
    recs = await fetch_recommendations(db_factory, plan.id)
    assert len(recs) == 1
    assert recs[0].canonical_job_id == job
    assert recs[0].hard_filter_status == "uncertain"
    salary_item = next(
        i for i in recs[0].hard_conditions_json if i["condition"] == "salary"
    )
    assert salary_item["status"] == "unknown"
    # 薪资低于最低要求的岗位必须被过滤（对照组）
    await create_job(db_factory, salary=(10000, 15000), title="低薪 Python 岗")
    run_matching(plan.id)
    recs = await fetch_recommendations(db_factory, plan.id)
    assert len(recs) == 1  # 低薪岗未进入


async def test_blocked_company_never_recommended(db_factory):
    user = await create_user(db_factory, "match-block@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    await create_job(db_factory, company_name="屏蔽外包集团", title="Python 开发 A")
    ok_job = await create_job(db_factory, company_name="正常科技公司", title="Python 开发 B")

    # 屏蔽公司偏好
    from sqlalchemy import select

    from app.db.models import Company, CompanyPreference

    async with db_factory() as db:
        company = (
            await db.execute(select(Company).where(Company.canonical_name == "屏蔽外包集团"))
        ).scalar_one()
        db.add(
            CompanyPreference(
                search_plan_id=plan.id, company_id=company.id, preference="block"
            )
        )
        await db.commit()

    run_matching(plan.id)
    recs = await fetch_recommendations(db_factory, plan.id)
    assert [r.canonical_job_id for r in recs] == [ok_job]


async def test_daily_limit_20_and_same_day_idempotent(db_factory):
    user = await create_user(db_factory, "match-limit@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    for i in range(25):
        await create_job(db_factory, title=f"Python 后端开发工程师 {i:02d}")

    stats = run_matching(plan.id, "2026-07-29")
    assert stats["created"] == 20  # 每日上限
    # 同日重跑：历史已推荐去重 → 不新增（唯一约束兜底并发）
    stats2 = run_matching(plan.id, "2026-07-29")
    assert stats2["created"] == 0
    recs = await fetch_recommendations(db_factory, plan.id)
    assert len(recs) == 20
    assert sorted(r.rank for r in recs) == list(range(1, 21))


async def test_no_repeat_recommendation_across_days(db_factory):
    """历史已推荐过的岗位次日不重复；新岗位正常进入。"""
    user = await create_user(db_factory, "match-repeat@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    await create_job(db_factory, title="Python 后端 D1")

    assert run_matching(plan.id, "2026-07-29")["created"] == 1
    assert run_matching(plan.id, "2026-07-30")["created"] == 0  # 不重复推荐
    await create_job(db_factory, title="Python 后端 D2 新岗位")
    stats = run_matching(plan.id, "2026-07-30")
    assert stats["created"] == 1
    recs = await fetch_recommendations(db_factory, plan.id)
    assert len(recs) == 2


async def test_minimum_match_score_filters(db_factory):
    """方案 minimum_match_score 过滤：方向完全错配的岗位分数不足被隐藏。"""
    user = await create_user(db_factory, "match-minscore@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user, minimum_match_score=65)
    await create_job(db_factory)  # 匹配岗
    await create_job(
        db_factory,
        title="资深售前顾问",
        description="负责大客户售前方案与投标支持，行业解决方案输出。",
        role_family="unknown",
        education=("master", "required"),
        experience=(5, 10),
    )  # 错配岗（学历/经验也不符 → 硬过滤失败）

    run_matching(plan.id)
    recs = await fetch_recommendations(db_factory, plan.id)
    assert len(recs) == 1
    assert recs[0].score_total >= 65


async def test_matching_logs_contain_no_job_or_fact_content(db_factory):
    """日志无正文：岗位描述与事实内容绝不出现在匹配任务日志。"""
    user = await create_user(db_factory, "match-logs@cc-integration.dev")
    await create_facts(db_factory, user)
    plan = await create_plan(db_factory, user)
    canary = "PRIVACYCANARY机密职责描述九三一"
    await create_job(db_factory, description=f"负责 FastAPI 服务开发。{canary}")

    with structlog.testing.capture_logs() as logs:
        run_matching(plan.id)
    dumped = repr(logs)
    assert canary not in dumped
    assert "星辰科技有限公司" not in dumped  # 事实内容也不进日志
    assert any(e.get("event") == "recommendations_generated" for e in logs)
