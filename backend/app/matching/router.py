"""推荐查询与反馈 API（docs/04 第 6 节）。

- 越权访问他人推荐一律 404（防 IDOR）。
- 列表默认隐藏 low 等级（<65，docs/07 第 4 节）；显式 grade=low 才返回。
- 反馈 note 只入库，绝不进日志/审计/第三方分析。
"""

import base64
import binascii
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import TypeAdapter
from sqlalchemy import ColumnElement, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, require_active_user, require_user
from app.core.errors import AppError
from app.core.security import hash_ip
from app.db.models import (
    CanonicalJob,
    Company,
    JobPosting,
    JobSource,
    MatchComponent,
    Recommendation,
    SearchPlan,
    UserFeedback,
)
from app.db.session import get_db
from app.jobs.constants import DATA_ORIGIN_USER_IMPORT
from app.matching.schemas import (
    FeedbackOut,
    FeedbackReasonCode,
    FeedbackRequest,
    FeedbackSentiment,
    HardConditionItemOut,
    MatchComponentOut,
    RecommendationDetailOut,
    RecommendationListOut,
    RecommendationSummaryOut,
    RiskSignalOut,
    SourceLinkOut,
)
from app.matching.service import apply_feedback_delta

router = APIRouter(tags=["recommendations"])

_NOT_FOUND = AppError(code="NOT_FOUND", message="资源不存在", status_code=404)

# DB 存 str（有 CHECK 约束），出参 Literal：运行时真校验，非静态断言
_SENTIMENT_ADAPTER: TypeAdapter[FeedbackSentiment] = TypeAdapter(FeedbackSentiment)
_REASON_ADAPTER: TypeAdapter[FeedbackReasonCode | None] = TypeAdapter(
    FeedbackReasonCode | None
)


def _encode_cursor(rec: Recommendation) -> str:
    raw = f"{rec.recommended_on.isoformat()}|{rec.rank}|{rec.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[date, int, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        day_s, rank_s, id_s = raw.split("|")
        return date.fromisoformat(day_s), int(rank_s), uuid.UUID(id_s)
    except (ValueError, binascii.Error) as exc:
        raise AppError(
            code="VALIDATION_ERROR", message="无效的分页游标", status_code=422
        ) from exc


def _job_visible_to(user_id: uuid.UUID) -> ColumnElement[bool]:
    """岗位对该用户可见：global，或 private 且属主本人（与 /jobs 系列同一规则）。

    岗位转私有/属主注销后，历史推荐不得继续暴露岗位内容（P-1 第 2 项）。
    """
    return or_(
        CanonicalJob.visibility == "global",
        CanonicalJob.owner_user_id == user_id,
    )


async def _get_owned_recommendation(
    db: AsyncSession, rec_id: uuid.UUID, ctx: AuthContext
) -> tuple[Recommendation, SearchPlan]:
    row = (
        await db.execute(
            select(Recommendation, SearchPlan)
            .join(SearchPlan, SearchPlan.id == Recommendation.search_plan_id)
            .join(CanonicalJob, CanonicalJob.id == Recommendation.canonical_job_id)
            .where(Recommendation.id == rec_id)
            .where(_job_visible_to(ctx.user.id))
        )
    ).first()
    if row is None or row[1].user_id != ctx.user.id:
        raise _NOT_FOUND  # 越权/岗位不可见与不存在同样返回 404，不泄露存在性
    return row[0], row[1]


async def _primary_posting(db: AsyncSession, job: CanonicalJob) -> JobPosting | None:
    if job.primary_posting_id is not None:
        posting = await db.get(JobPosting, job.primary_posting_id)
        if posting is not None:
            return posting
    return (
        await db.execute(
            select(JobPosting)
            .where(JobPosting.canonical_job_id == job.id)
            .order_by(JobPosting.first_seen_at)
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("/recommendations", response_model=RecommendationListOut)
async def list_recommendations(
    plan_id: uuid.UUID | None = None,
    on_date: date | None = Query(default=None, alias="date"),
    grade: str | None = Query(default=None, pattern="^(high|potential|low)$"),
    hard_filter_status: str | None = Query(default=None, pattern="^(passed|uncertain)$"),
    cursor: str | None = None,
    limit: int = Query(default=20, ge=1, le=50),
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationListOut:
    stmt = (
        select(Recommendation, SearchPlan)
        .join(SearchPlan, SearchPlan.id == Recommendation.search_plan_id)
        .join(CanonicalJob, CanonicalJob.id == Recommendation.canonical_job_id)
        .where(SearchPlan.user_id == ctx.user.id)
        .where(_job_visible_to(ctx.user.id))
        .order_by(
            Recommendation.recommended_on.desc(),
            Recommendation.rank.asc(),
            Recommendation.id.asc(),
        )
        .limit(limit + 1)
    )
    if plan_id is not None:
        stmt = stmt.where(Recommendation.search_plan_id == plan_id)
    if on_date is not None:
        stmt = stmt.where(Recommendation.recommended_on == on_date)
    if grade is not None:
        stmt = stmt.where(Recommendation.grade == grade)
    else:
        # low（<65）默认隐藏（docs/07 第 4 节）
        stmt = stmt.where(Recommendation.grade != "low")
    if hard_filter_status is not None:
        stmt = stmt.where(Recommendation.hard_filter_status == hard_filter_status)
    if cursor is not None:
        c_day, c_rank, c_id = _decode_cursor(cursor)
        stmt = stmt.where(
            (Recommendation.recommended_on < c_day)
            | (
                (Recommendation.recommended_on == c_day)
                & (
                    (Recommendation.rank > c_rank)
                    | ((Recommendation.rank == c_rank) & (Recommendation.id > c_id))
                )
            )
        )

    rows = (await db.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    items: list[RecommendationSummaryOut] = []
    for rec, _plan in rows:
        job = await db.get(CanonicalJob, rec.canonical_job_id)
        posting = await _primary_posting(db, job) if job else None
        company_name = None
        if posting is not None and posting.company_id is not None:
            company = await db.get(Company, posting.company_id)
            company_name = company.canonical_name if company else None
        feedback = (
            await db.execute(
                select(UserFeedback).where(UserFeedback.recommendation_id == rec.id)
            )
        ).scalar_one_or_none()
        items.append(
            RecommendationSummaryOut(
                id=rec.id,
                search_plan_id=rec.search_plan_id,
                canonical_job_id=rec.canonical_job_id,
                job_title=(posting.title_raw if posting else (job.title_normalized if job else "")),
                company_name=company_name,
                city_code=posting.city_code if posting else None,
                data_origin=(job.data_origin if job else DATA_ORIGIN_USER_IMPORT),
                score=rec.score_total,
                grade=rec.grade,
                hard_filter_status=rec.hard_filter_status,
                rank=rec.rank,
                recommended_on=rec.recommended_on,
                feedback_sentiment=(
                    _SENTIMENT_ADAPTER.validate_python(feedback.sentiment)
                    if feedback
                    else None
                ),
            )
        )
    next_cursor = _encode_cursor(rows[-1][0]) if has_more and rows else None
    return RecommendationListOut(items=items, next_cursor=next_cursor)


@router.get("/recommendations/{rec_id}", response_model=RecommendationDetailOut)
async def get_recommendation(
    rec_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> RecommendationDetailOut:
    rec, _plan = await _get_owned_recommendation(db, rec_id, ctx)
    job = await db.get(CanonicalJob, rec.canonical_job_id)
    posting = await _primary_posting(db, job) if job else None
    company_name = None
    if posting is not None and posting.company_id is not None:
        company = await db.get(Company, posting.company_id)
        company_name = company.canonical_name if company else None

    components = (
        (
            await db.execute(
                select(MatchComponent)
                .where(MatchComponent.recommendation_id == rec.id)
                .order_by(MatchComponent.component)
            )
        )
        .scalars()
        .all()
    )
    # 风险信号单独展示，不混入能力分（docs/07 第 4 节）
    risks = [
        RiskSignalOut(
            code=str(s.get("code", "UNKNOWN")),
            keyword=s.get("keyword"),
            evidence=str(s.get("evidence", "")),
            confidence=s.get("confidence"),
        )
        for s in (posting.outsourcing_signals if posting else []) or []
    ]
    # 纵深防御：他人导入的 posting（个人来源记录）绝不出现在来源链接里
    link_rows = (
        await db.execute(
            select(JobPosting, JobSource)
            .join(JobSource, JobSource.id == JobPosting.job_source_id)
            .where(
                JobPosting.canonical_job_id == rec.canonical_job_id,
                or_(
                    JobPosting.imported_by_user_id.is_(None),
                    JobPosting.imported_by_user_id == ctx.user.id,
                ),
            )
            .order_by(JobPosting.first_seen_at)
        )
    ).all()
    source_links = [
        SourceLinkOut(source_key=src.source_key, source_name=src.name, url=p.source_url)
        for p, src in link_rows
    ]
    feedback = (
        await db.execute(
            select(UserFeedback).where(UserFeedback.recommendation_id == rec.id)
        )
    ).scalar_one_or_none()

    return RecommendationDetailOut(
        id=rec.id,
        search_plan_id=rec.search_plan_id,
        canonical_job_id=rec.canonical_job_id,
        job_title=(posting.title_raw if posting else (job.title_normalized if job else "")),
        company_name=company_name,
        city_code=posting.city_code if posting else None,
        data_origin=(job.data_origin if job else DATA_ORIGIN_USER_IMPORT),
        description_text=posting.description_text if posting else None,
        salary_raw=posting.salary_raw if posting else None,
        score=rec.score_total,
        grade=rec.grade,
        hard_filter_status=rec.hard_filter_status,
        hard_conditions=[HardConditionItemOut(**item) for item in rec.hard_conditions_json],
        components=[
            MatchComponentOut(
                component=c.component,
                score=c.score,
                weight=c.weight,
                evidence_refs=list(c.evidence_refs_json),
                gap_level=c.gap_level,
                uncertainty=c.uncertainty,
            )
            for c in components
        ],
        risks=risks,
        source_links=source_links,
        scoring_version=rec.scoring_version,
        versions=dict(rec.versions_json),
        rank=rec.rank,
        recommended_on=rec.recommended_on,
        feedback=(
            FeedbackOut(
                recommendation_id=rec.id,
                sentiment=_SENTIMENT_ADAPTER.validate_python(feedback.sentiment),
                reason_code=_REASON_ADAPTER.validate_python(feedback.reason_code),
                note=feedback.optional_note,
                created_at=feedback.created_at,
            )
            if feedback
            else None
        ),
    )


@router.post(
    "/recommendations/{rec_id}/feedback",
    response_model=FeedbackOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_feedback(
    rec_id: uuid.UUID,
    payload: FeedbackRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackOut:
    """写入/更新反馈（每条推荐一条）；不感兴趣必须带 9 类结构化原因之一。"""
    rec, _plan = await _get_owned_recommendation(db, rec_id, ctx)
    if payload.sentiment == "not_interested" and payload.reason_code is None:
        raise AppError(
            code="VALIDATION_ERROR",
            message="不感兴趣反馈必须选择结构化原因",
            status_code=422,
        )
    existing = (
        await db.execute(
            select(UserFeedback).where(UserFeedback.recommendation_id == rec.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = UserFeedback(
            recommendation_id=rec.id,
            sentiment=payload.sentiment,
            reason_code=payload.reason_code,
            optional_note=payload.note,
        )
        db.add(existing)
    else:
        # 替换旧反馈：先回退旧计数，再累计新计数
        await apply_feedback_delta(
            db,
            ctx.user.id,
            sentiment=existing.sentiment,
            reason_code=existing.reason_code,
            delta=-1,
        )
        existing.sentiment = payload.sentiment
        existing.reason_code = payload.reason_code
        existing.optional_note = payload.note
        existing.created_at = datetime.now(UTC)
    await db.flush()
    await apply_feedback_delta(
        db,
        ctx.user.id,
        sentiment=payload.sentiment,
        reason_code=payload.reason_code,
        delta=1,
    )
    # 审计只记 ID 与原因码，绝不含 note 正文
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="recommendation_feedback_submitted",
        resource_type="recommendation",
        resource_id=str(rec.id),
        reason_code=f"{payload.sentiment}:{payload.reason_code or 'none'}",
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return FeedbackOut(
        recommendation_id=rec.id,
        sentiment=_SENTIMENT_ADAPTER.validate_python(existing.sentiment),
        reason_code=_REASON_ADAPTER.validate_python(existing.reason_code),
        note=existing.optional_note,
        created_at=existing.created_at,
    )


@router.delete("/recommendations/{rec_id}/feedback")
async def withdraw_feedback(
    rec_id: uuid.UUID,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    """撤回反馈并重算学习偏好。"""
    rec, _plan = await _get_owned_recommendation(db, rec_id, ctx)
    existing = (
        await db.execute(
            select(UserFeedback).where(UserFeedback.recommendation_id == rec.id)
        )
    ).scalar_one_or_none()
    if existing is None:
        raise _NOT_FOUND
    await db.execute(delete(UserFeedback).where(UserFeedback.id == existing.id))
    await apply_feedback_delta(
        db,
        ctx.user.id,
        sentiment=existing.sentiment,
        reason_code=existing.reason_code,
        delta=-1,
    )
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="recommendation_feedback_withdrawn",
        resource_type="recommendation",
        resource_id=str(rec.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return {"status": "withdrawn"}
