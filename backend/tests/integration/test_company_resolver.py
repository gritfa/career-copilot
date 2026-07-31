"""公司归一解析器集成测试（真实 PG）。

覆盖任务书要求的四类场景：
1. 常见别名归一（配置别名 / normalized 写法变体 → 同一公司）。
2. 同名不同主体不合并（core 相同 / normalized 多义 → 只进待审，绝不自动合并）。
3. 空 / 异常名不污染公司主数据。
4. 人工修正可追溯（approve/reject 留 decided_by、规则版本，岗位归属改指可查）。
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.companies.normalize import COMPANY_NAME_RULES_VERSION
from app.companies.resolver import decide_alias_review, resolve_company
from app.db.models import Company, CompanyAliasReview, JobPosting, JobSource
from tests.integration.conftest import PG_HOST, PG_PASSWORD, PG_PORT, PG_USER, TEST_DB

SYNC_URL = f"postgresql+psycopg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{TEST_DB}"


@pytest.fixture
def sync_db(real_env):
    """resolver 是同步实现（岗位管道用），测试直连同步引擎。"""
    engine = create_engine(SYNC_URL, poolclass=NullPool)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _company_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Company)).scalar_one()


# ---------------- 1) 常见别名归一 ----------------


def test_alias_config_unifies_known_aliases(sync_db):
    r1 = resolve_company(sync_db, "ByteDance")
    assert r1.company is not None
    assert r1.matched_by == "alias_config"
    assert r1.company.canonical_name == "字节跳动"

    r2 = resolve_company(sync_db, "北京字节跳动科技有限公司")
    r3 = resolve_company(sync_db, "字节跳动")
    assert r2.company is not None and r3.company is not None
    assert r2.company.id == r1.company.id
    assert r3.company.id == r1.company.id
    assert _company_count(sync_db) == 1

    # 命中写法留档为结构化别名，带规则版本
    alias_entries = [a for a in r1.company.aliases if isinstance(a, dict)]
    assert any(a["alias"] == "ByteDance" for a in alias_entries)
    assert all(a["rules_version"] == COMPANY_NAME_RULES_VERSION for a in alias_entries)


def test_normalized_variants_unify_without_review(sync_db):
    base = resolve_company(sync_db, "星岚科技")
    assert base.company is not None and base.created

    variant = resolve_company(sync_db, "  星岚 科技 ")
    assert variant.company is not None
    assert variant.matched_by == "normalized"
    assert variant.company.id == base.company.id
    assert _company_count(sync_db) == 1
    # 高置信归一不开待审
    assert (
        sync_db.execute(select(func.count()).select_from(CompanyAliasReview)).scalar_one()
        == 0
    )


# ---------------- 2) 同名不同主体不合并 ----------------


def test_core_match_opens_review_never_merges(sync_db):
    a = resolve_company(sync_db, "云帆网络科技有限公司")
    assert a.company is not None

    b = resolve_company(sync_db, "杭州云帆信息技术有限公司")
    assert b.company is not None
    # 仅 core 相似：新建独立公司，绝不并入 a
    assert b.company.id != a.company.id
    assert b.matched_by == "created"
    assert _company_count(sync_db) == 2

    review = sync_db.execute(select(CompanyAliasReview)).scalar_one()
    assert review.status == "pending"
    assert review.company_id == b.company.id
    assert review.candidate_company_id == a.company.id
    assert review.match_rule == "core_match"
    assert review.rules_version == COMPANY_NAME_RULES_VERSION
    assert review.evidence_json["candidate_canonical"] == "云帆网络科技有限公司"

    # 重复解析同一原始名：幂等，不重复开待审
    again = resolve_company(sync_db, "杭州云帆信息技术有限公司")
    assert again.company is not None and again.company.id == b.company.id
    assert (
        sync_db.execute(select(func.count()).select_from(CompanyAliasReview)).scalar_one()
        == 1
    )


def test_same_name_different_entities_not_merged(sync_db):
    # 两个真实存在的不同法律主体，名称仅地区括号不同
    first = resolve_company(sync_db, "云智科技（北京）有限公司")
    second = resolve_company(sync_db, "云智科技（上海）有限公司")
    assert first.company is not None and second.company is not None
    assert first.company.id != second.company.id

    # 第三方来源只写品牌名：core 命中两家 → 新建 + 两条待审，绝不自动挑一家合并
    brand = resolve_company(sync_db, "云智科技")
    assert brand.company is not None
    assert brand.created
    assert brand.company.id not in (first.company.id, second.company.id)
    assert len(brand.review_ids) == 2
    assert _company_count(sync_db) == 3


def test_ambiguous_normalized_goes_to_review(sync_db):
    first = resolve_company(sync_db, "云启数据")
    assert first.company is not None
    # 构造 normalized 键完全相同的第二家既有公司（同名不同主体的极端脏数据）
    second = Company(
        id=uuid.uuid4(),
        canonical_name="云启数据（成都）",
        aliases=[],
        name_normalized="云启数据",
        name_core="云启数据",
        name_rules_version=COMPANY_NAME_RULES_VERSION,
        source_refs_json=[],
    )
    sync_db.add(second)
    sync_db.flush()

    # 写法变体 normalized 命中两家 → 多义，不自动挑一家
    result = resolve_company(sync_db, "云启 数据")
    assert result.company is not None
    assert result.matched_by == "created"
    assert result.company.id not in (first.company.id, second.id)
    reviews = sync_db.execute(select(CompanyAliasReview)).scalars().all()
    assert {r.match_rule for r in reviews} == {"ambiguous_normalized"}
    assert len(reviews) == 2


# ---------------- 3) 空 / 异常名不污染 ----------------


@pytest.mark.parametrize("raw", [None, "", "   ", "N/A", "无", "未知", "保密", "###"])
def test_invalid_names_do_not_pollute(sync_db, raw):
    result = resolve_company(sync_db, raw)
    assert result.company is None
    assert result.matched_by == "rejected_invalid"
    assert _company_count(sync_db) == 0


# ---------------- 4) 人工修正可追溯 ----------------


def _make_posting(db: Session, company_id: uuid.UUID) -> JobPosting:
    source = JobSource(
        id=uuid.uuid4(),
        source_key=f"fixture_resolver_{uuid.uuid4().hex[:6]}",
        name="resolver 测试来源",
        source_type="company_site",
        status="enabled",
    )
    db.add(source)
    db.flush()
    now = datetime.now(UTC)
    posting = JobPosting(
        id=uuid.uuid4(),
        job_source_id=source.id,
        source_job_id=uuid.uuid4().hex,
        company_id=company_id,
        title_raw="后端开发工程师",
        title_normalized="后端开发工程师",
        normalizer_version="1",
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(posting)
    db.flush()
    return posting


def test_manual_approval_is_traceable_and_repoints_jobs(sync_db):
    target = resolve_company(sync_db, "云帆网络科技有限公司")
    dup = resolve_company(sync_db, "杭州云帆信息技术有限公司")
    assert target.company is not None and dup.company is not None
    posting = _make_posting(sync_db, dup.company.id)

    review = sync_db.execute(select(CompanyAliasReview)).scalar_one()
    decide_alias_review(
        sync_db, review, approve=True, decided_by="ops@careercopilot", note="同一集团品牌"
    )
    sync_db.commit()

    # 裁决可追溯
    assert review.status == "approved"
    assert review.decided_by == "ops@careercopilot"
    assert review.decided_at is not None
    assert review.resolution_note == "同一集团品牌"

    # 别名落到目标公司且带 review_id / 决策人
    sync_db.refresh(target.company)
    manual_aliases = [
        a
        for a in target.company.aliases
        if isinstance(a, dict) and a.get("source") == "manual_review"
    ]
    assert any(a["alias"] == "杭州云帆信息技术有限公司" for a in manual_aliases)
    assert all(a["review_id"] == str(review.id) for a in manual_aliases)
    assert all(a["decided_by"] == "ops@careercopilot" for a in manual_aliases)

    # 岗位归属改指目标公司；被并方留痕 merged_into
    sync_db.refresh(posting)
    assert posting.company_id == target.company.id
    sync_db.refresh(dup.company)
    merge_refs = [
        r
        for r in dup.company.source_refs_json
        if isinstance(r, dict) and r.get("merged_into")
    ]
    assert merge_refs and merge_refs[0]["merged_into"] == str(target.company.id)
    assert merge_refs[0]["review_id"] == str(review.id)

    # 被并方不再参与 normalized 命中；exact 命中旧 canonical 时经重定向归目标
    after = resolve_company(sync_db, "杭州云帆信息技术有限公司")
    assert after.company is not None
    assert after.company.id == target.company.id
    # 已裁决对不再重复开待审
    assert (
        sync_db.execute(
            select(func.count())
            .select_from(CompanyAliasReview)
            .where(CompanyAliasReview.status == "pending")
        ).scalar_one()
        == 0
    )

    # 不允许重复裁决
    with pytest.raises(ValueError):
        decide_alias_review(sync_db, review, approve=False, decided_by="ops2")


def test_manual_rejection_keeps_entities_separate(sync_db):
    a = resolve_company(sync_db, "云智科技（北京）有限公司")
    b = resolve_company(sync_db, "云智科技（上海）有限公司")
    assert a.company is not None and b.company is not None

    review = sync_db.execute(select(CompanyAliasReview)).scalar_one()
    decide_alias_review(
        sync_db, review, approve=False, decided_by="ops@careercopilot", note="不同法律主体"
    )
    sync_db.commit()

    assert review.status == "rejected"
    assert review.decided_by == "ops@careercopilot"
    # 双方公司原样保留，无别名互写
    sync_db.refresh(a.company)
    sync_db.refresh(b.company)
    assert a.company.aliases == []
    assert b.company.aliases == []
    assert _company_count(sync_db) == 2
