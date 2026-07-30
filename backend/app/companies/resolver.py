"""公司归一解析器（阶段 10 任务 D，同步 Session，供岗位管道调用）。

匹配阶梯（置信度由高到低）：
1. canonical_name 精确匹配（原有行为，完全兼容）。
2. 版本化别名配置（aliases_v1.yaml，人工白名单）命中 → 归一到 canonical。
3. name_normalized（空白/全半角/括号/大小写规范化后）唯一命中 → 同名变体，记别名证据。
4. name_core（去法律/行业后缀、地区前缀）命中 → **低置信**：新建独立公司 +
   company_alias_reviews 待审记录，绝不自动合并。
5. 全不命中 → 新建公司。

硬边界：
- 绝不凭字符串相似度自动合并；normalized 多义（同名不同主体）同样只进待审。
- 垃圾/占位名返回 None，不建公司。
- 所有自动归一在 aliases / source_refs_json 留 raw、规则、规则版本，人工审核留
  decided_by / decided_at，全程可追溯。
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.companies.aliases import load_alias_config
from app.companies.normalize import CompanyNameNorm, normalize_company_name
from app.db.models import CanonicalJob, Company, CompanyAliasReview, JobPosting

logger = structlog.get_logger("app.companies.resolver")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def company_name_fields(name: str) -> dict[str, str | None]:
    """给直接创建 Company 的调用方（如 search_plans）补齐标准化键。"""
    norm = normalize_company_name(name)
    if norm is None:
        return {"name_normalized": None, "name_core": None, "name_rules_version": None}
    return {
        "name_normalized": norm.normalized,
        "name_core": norm.core,
        "name_rules_version": norm.rules_version,
    }


@dataclass(frozen=True)
class CompanyResolution:
    company: Company | None
    matched_by: str  # rejected_invalid | exact | alias_config | normalized | created
    created: bool = False
    review_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)


def _follow_merge(db: Session, company: Company, max_hops: int = 5) -> Company:
    """跟随人工审核产生的并入重定向（行保留可追溯，解析结果归并入目标）。"""
    current = company
    for _ in range(max_hops):
        if current.merged_into_company_id is None:
            return current
        target = db.get(Company, current.merged_into_company_id)
        if target is None:
            return current
        current = target
    return current


def _alias_raw_values(company: Company) -> set[str]:
    """aliases JSONB 同时容忍历史字符串条目和结构化条目。"""
    values: set[str] = set()
    for item in company.aliases or []:
        if isinstance(item, str):
            values.add(item)
        elif isinstance(item, dict) and item.get("alias"):
            values.add(str(item["alias"]))
    return values


def _record_alias(company: Company, raw_name: str, rule: str, norm: CompanyNameNorm) -> None:
    """把自动归一命中的原始写法留档为结构化别名（幂等）。"""
    if raw_name == company.canonical_name or raw_name in _alias_raw_values(company):
        return
    entry = {
        "alias": raw_name,
        "source": rule,
        "rules_version": norm.rules_version,
        "added_at": _utcnow().isoformat(),
    }
    company.aliases = [*(company.aliases or []), entry]


def _new_company(db: Session, norm: CompanyNameNorm) -> Company:
    company = Company(
        id=uuid.uuid4(),
        canonical_name=norm.display[:255],
        aliases=[],
        name_normalized=norm.normalized,
        name_core=norm.core,
        name_rules_version=norm.rules_version,
        source_refs_json=[],
    )
    db.add(company)
    db.flush()
    return company


def _open_review(
    db: Session,
    *,
    company: Company,
    candidate: Company,
    norm: CompanyNameNorm,
    match_rule: str,
) -> uuid.UUID | None:
    """开低置信待审记录；同一公司对幂等（已有记录则不重复开）。"""
    existing = db.execute(
        select(CompanyAliasReview.id).where(
            CompanyAliasReview.company_id == company.id,
            CompanyAliasReview.candidate_company_id == candidate.id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None
    review = CompanyAliasReview(
        id=uuid.uuid4(),
        raw_name=norm.raw[:255],
        normalized_name=norm.normalized,
        core_name=norm.core,
        company_id=company.id,
        candidate_company_id=candidate.id,
        match_rule=match_rule,
        rules_version=norm.rules_version,
        evidence_json={
            "applied_rules": list(norm.applied_rules),
            "company_canonical": company.canonical_name,
            "candidate_canonical": candidate.canonical_name,
        },
        status="pending",
    )
    db.add(review)
    db.flush()
    logger.info(
        "company_alias_review_opened",
        review_id=str(review.id),
        match_rule=match_rule,
        company_id=str(company.id),
        candidate_company_id=str(candidate.id),
    )
    return review.id


def resolve_company(db: Session, raw_name: str | None) -> CompanyResolution:
    """公司原始名 → 归一后的 Company（或 None）。不 commit，由调用方事务提交。"""
    norm = normalize_company_name(raw_name)
    if norm is None:
        return CompanyResolution(company=None, matched_by="rejected_invalid")

    # 1) canonical 精确命中
    company = db.execute(
        select(Company).where(Company.canonical_name == norm.display)
    ).scalar_one_or_none()
    if company is not None:
        return CompanyResolution(company=_follow_merge(db, company), matched_by="exact")

    # 2) 版本化别名配置（人工白名单）
    config = load_alias_config()
    canonical_from_alias = config.alias_to_canonical.get(norm.normalized)
    if canonical_from_alias is not None:
        company = db.execute(
            select(Company).where(Company.canonical_name == canonical_from_alias)
        ).scalar_one_or_none()
        if company is None:
            canonical_norm = normalize_company_name(canonical_from_alias)
            if canonical_norm is None:  # 配置加载时已校验，此处防御
                raise ValueError(f"别名配置 canonical 非法: {canonical_from_alias!r}")
            company = _new_company(db, canonical_norm)
        company = _follow_merge(db, company)
        _record_alias(company, norm.raw.strip(), f"alias_config_v{config.version}", norm)
        return CompanyResolution(company=company, matched_by="alias_config")

    # 3) normalized 键命中（同名写法变体）；多义 = 同名不同主体，绝不自动挑一个
    normalized_matches = (
        db.execute(select(Company).where(Company.name_normalized == norm.normalized))
        .scalars()
        .all()
    )
    if len(normalized_matches) == 1:
        company = _follow_merge(db, normalized_matches[0])
        _record_alias(company, norm.raw.strip(), "normalized_match", norm)
        return CompanyResolution(company=company, matched_by="normalized")
    if len(normalized_matches) > 1:
        company = _new_company(db, norm)
        review_ids = [
            rid
            for cand in normalized_matches
            if (
                rid := _open_review(
                    db,
                    company=company,
                    candidate=cand,
                    norm=norm,
                    match_rule="ambiguous_normalized",
                )
            )
            is not None
        ]
        return CompanyResolution(
            company=company, matched_by="created", created=True, review_ids=tuple(review_ids)
        )

    # 4) core 键命中：低置信候选 → 新建独立公司 + 待审记录（不合并）
    core_matches = (
        db.execute(select(Company).where(Company.name_core == norm.core)).scalars().all()
    )
    company = _new_company(db, norm)
    review_ids = [
        rid
        for cand in core_matches
        if (
            rid := _open_review(
                db, company=company, candidate=cand, norm=norm, match_rule="core_match"
            )
        )
        is not None
    ]
    return CompanyResolution(
        company=company, matched_by="created", created=True, review_ids=tuple(review_ids)
    )


def decide_alias_review(
    db: Session,
    review: CompanyAliasReview,
    *,
    approve: bool,
    decided_by: str,
    note: str | None = None,
) -> None:
    """人工裁决待审记录（可追溯）。

    approve=True：company（新建方）的岗位归属全部改指 candidate（既有目标），
    raw/canonical 写法作为结构化别名留在目标公司；被并方公司行保留并在
    source_refs_json 记 merged_into，历史可查。
    approve=False：两个主体判定为不同公司，各自保留。
    """
    if review.status != "pending":
        raise ValueError(f"review {review.id} 已裁决过（{review.status}），不可重复裁决")
    now = _utcnow()
    review.status = "approved" if approve else "rejected"
    review.decided_at = now
    review.decided_by = decided_by
    review.resolution_note = note

    if not approve:
        db.flush()
        return

    source = db.get(Company, review.company_id)
    target = db.get(Company, review.candidate_company_id)
    if source is None or target is None:
        raise ValueError(f"review {review.id} 关联公司缺失，无法执行合并")

    for alias_raw in {review.raw_name, source.canonical_name}:
        if alias_raw == target.canonical_name or alias_raw in _alias_raw_values(target):
            continue
        target.aliases = [
            *(target.aliases or []),
            {
                "alias": alias_raw,
                "source": "manual_review",
                "review_id": str(review.id),
                "rules_version": review.rules_version,
                "decided_by": decided_by,
                "added_at": now.isoformat(),
            },
        ]

    db.execute(
        update(JobPosting)
        .where(JobPosting.company_id == source.id)
        .values(company_id=target.id)
    )
    db.execute(
        update(CanonicalJob)
        .where(CanonicalJob.company_id == source.id)
        .values(company_id=target.id)
    )
    # 被并方保留行（外键安全）：清匹配键防止再被 normalized/core 命中，
    # 并设重定向指针，exact 命中旧 canonical 时归并入目标
    source.name_normalized = None
    source.name_core = None
    source.merged_into_company_id = target.id
    source.source_refs_json = [
        *(source.source_refs_json or []),
        {
            "merged_into": str(target.id),
            "review_id": str(review.id),
            "decided_by": decided_by,
            "decided_at": now.isoformat(),
        },
    ]
    db.flush()
    logger.info(
        "company_alias_review_decided",
        review_id=str(review.id),
        approved=approve,
        source_company_id=str(source.id),
        target_company_id=str(target.id),
    )
