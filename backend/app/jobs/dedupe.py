"""分层去重（docs/06 第 8 节）。

1. 同来源 source_job_id / URL：确定性重复（pipeline 的 upsert 层处理）。
2. 公司规范名 + 标准职位 + 城市：候选重复。
3. 职责文本相似度（difflib）辅助判断。
4. 高置信自动合并 canonical_job；中置信标记 pending_review 待人工/管理员队列。

合并后企业官网优先作为主来源；所有来源 posting 链接全部保留，绝不删除。
"""

import uuid
from difflib import SequenceMatcher

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import CanonicalJob, JobPosting, JobSource

logger = structlog.get_logger("app.jobs.dedupe")

DEDUPE_VERSION = "1"

# 文本相似度阈值：>= HIGH 自动合并；[MID, HIGH) 进待审队列；< MID 视为不同岗位
HIGH_CONFIDENCE = 0.85
MID_CONFIDENCE = 0.60
# 键（公司+标准职位+城市）完全一致但缺少正文可比时的保守置信度
KEY_ONLY_CONFIDENCE = 0.70


def text_similarity(a: str | None, b: str | None) -> float | None:
    """职责/要求文本相似度；任一方缺失返回 None（无法辅助判断）。"""
    if not a or not b:
        return None
    return SequenceMatcher(None, a, b).ratio()


def _find_merge_candidates(db: Session, posting: JobPosting) -> list[JobPosting]:
    """第 2 层候选重复：同公司规范名 + 标准职位 + 城市，且来自其他来源。"""
    if posting.company_id is None:
        return []
    stmt = (
        select(JobPosting)
        .where(
            JobPosting.company_id == posting.company_id,
            JobPosting.title_normalized == posting.title_normalized,
            JobPosting.id != posting.id,
            JobPosting.canonical_job_id.is_not(None),
        )
        .order_by(JobPosting.first_seen_at)
    )
    if posting.city_code is not None:
        stmt = stmt.where(JobPosting.city_code == posting.city_code)
    else:
        stmt = stmt.where(JobPosting.city_code.is_(None))
    return list(db.execute(stmt).scalars().all())


def _source_type(db: Session, posting: JobPosting) -> str:
    source = db.get(JobSource, posting.job_source_id)
    return source.source_type if source else "user_import"


def _maybe_promote_primary(db: Session, canonical: CanonicalJob, posting: JobPosting) -> None:
    """企业官网优先作为主展示来源；其他来源仍全部保留。"""
    if _source_type(db, posting) != "company_site":
        return
    primary: JobPosting | None = None
    if canonical.primary_posting_id is not None:
        primary = db.get(JobPosting, canonical.primary_posting_id)
    if primary is None or _source_type(db, primary) != "company_site":
        canonical.primary_posting_id = posting.id


def _create_canonical(db: Session, posting: JobPosting, confidence: float) -> CanonicalJob:
    canonical = CanonicalJob(
        id=uuid.uuid4(),
        title_normalized=posting.title_normalized,
        role_family=posting.role_family,
        company_id=posting.company_id,
        city_code=posting.city_code,
        primary_posting_id=posting.id,
        dedupe_version=DEDUPE_VERSION,
        dedupe_confidence=confidence,
        review_status="auto",
        status="active",
        first_seen_at=posting.first_seen_at,
    )
    db.add(canonical)
    db.flush()
    posting.canonical_job_id = canonical.id
    posting.dedupe_status = "unique"
    return canonical


def assign_canonical(db: Session, posting: JobPosting) -> CanonicalJob | None:
    """为一条（全职）posting 分配 canonical job：合并 / 待审 / 新建。

    返回归属的 canonical（pending_review 时返回 None——待审期间不并池）。
    """
    candidates = _find_merge_candidates(db, posting)

    best: tuple[JobPosting, float] | None = None
    for cand in candidates:
        ratio = text_similarity(posting.description_text, cand.description_text)
        confidence = KEY_ONLY_CONFIDENCE if ratio is None else ratio
        if best is None or confidence > best[1]:
            best = (cand, confidence)

    if best is not None:
        cand, confidence = best
        if confidence >= HIGH_CONFIDENCE:
            canonical = db.get(CanonicalJob, cand.canonical_job_id)
            if canonical is not None:
                posting.canonical_job_id = canonical.id
                posting.dedupe_status = "merged"
                posting.dedupe_candidate_canonical_id = None
                canonical.dedupe_confidence = confidence
                canonical.dedupe_version = DEDUPE_VERSION
                _maybe_promote_primary(db, canonical, posting)
                logger.info(
                    "job_dedupe_merged",
                    posting_id=str(posting.id),
                    canonical_id=str(canonical.id),
                    confidence=round(confidence, 3),
                )
                return canonical
        elif confidence >= MID_CONFIDENCE:
            # 中置信：进待审队列，不自动并池也不新建重复 canonical
            posting.dedupe_status = "pending_review"
            posting.dedupe_candidate_canonical_id = cand.canonical_job_id
            posting.canonical_job_id = None
            logger.info(
                "job_dedupe_pending_review",
                posting_id=str(posting.id),
                candidate_canonical_id=str(cand.canonical_job_id),
                confidence=round(confidence, 3),
            )
            return None

    return _create_canonical(db, posting, 1.0)
