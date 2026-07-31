"""岗位导入 / 来源链接 / 有效性检查 API（docs/04 第 6 节）。

安全边界：
- URL 导入只存引用，绝不服务端抓取（docs/06 硬边界 + ADR D1）。
- 正文导入走与连接器完全相同的 snapshot → normalize → dedupe 管道。
- 审计/日志只含 ID 与状态，严禁岗位正文。
- 有效性检查低频限流（Redis 固定窗口）。
"""

import asyncio
import uuid
from typing import Literal

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import Engine, create_engine, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.audit.service import record_audit
from app.auth.deps import AuthContext, require_active_user, require_user
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis_dep
from app.core.security import hash_ip
from app.db.models import CanonicalJob, JobPosting, JobSource
from app.db.session import get_db
from app.jobs.adapters.base import ValidityStatus
from app.jobs.constants import SOURCE_KEY_USER_IMPORT
from app.jobs.pipeline import (
    IngestResult,
    build_import_text_raw,
    import_url_reference,
    ingest_raw,
)
from app.jobs.registry import get_adapter, seed_sources_sync
from app.jobs.schemas import (
    JobImportOut,
    JobImportRequest,
    JobSourceLinkOut,
    JobSourcesOut,
    ValidityCheckOut,
)

logger = structlog.get_logger("app.jobs.router")

router = APIRouter(tags=["jobs"])

_VALIDITY_CHECK_LIMIT_PER_HOUR = 10


def _sync_session() -> tuple[Session, Engine]:
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    return Session(engine), engine


def _import_text_sync(payload: JobImportRequest, user_id: uuid.UUID) -> IngestResult:
    """线程内执行：与连接器同一条同步管道（snapshot → posting → canonical）。"""
    db, engine = _sync_session()
    try:
        seed_sources_sync(db)
        source = db.execute(
            select(JobSource).where(JobSource.source_key == SOURCE_KEY_USER_IMPORT)
        ).scalar_one()
        raw = build_import_text_raw(
            title=payload.title or "",
            company=payload.company_name,
            city=payload.city,
            salary=payload.salary_text,
            experience=payload.experience_text,
            education=payload.education_text,
            employment=payload.employment_text,
            description=payload.description_text or "",
            url=str(payload.url) if payload.url else None,
            imported_by_user_id=user_id,
        )
        result = ingest_raw(db, source, raw, imported_by_user_id=user_id)
        db.commit()
        return result
    finally:
        db.close()
        engine.dispose()


def _import_url_sync(url: str, title: str | None, user_id: uuid.UUID) -> IngestResult:
    db, engine = _sync_session()
    try:
        seed_sources_sync(db)
        source = db.execute(
            select(JobSource).where(JobSource.source_key == SOURCE_KEY_USER_IMPORT)
        ).scalar_one()
        posting, created = import_url_reference(db, source, url, user_id, title=title)
        db.commit()
        return IngestResult(
            posting_id=posting.id,
            canonical_job_id=posting.canonical_job_id,
            dedupe_status=posting.dedupe_status,
            created=created,
            changed=created,
        )
    finally:
        db.close()
        engine.dispose()


@router.post("/jobs/import", response_model=JobImportOut, status_code=status.HTTP_201_CREATED)
async def import_job(
    payload: JobImportRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> JobImportOut:
    """用户导入岗位：正文走完整管道；URL 只存引用不抓取。"""
    imported_via: Literal["text", "url"]
    if payload.description_text is not None:
        imported_via = "text"
        result = await asyncio.to_thread(_import_text_sync, payload, ctx.user.id)
    else:
        imported_via = "url"
        result = await asyncio.to_thread(
            _import_url_sync, str(payload.url), payload.title, ctx.user.id
        )

    # 审计不含任何岗位正文/URL 明细
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="job_imported",
        resource_type="job_posting",
        resource_id=str(result.posting_id),
        reason_code=f"via:{imported_via};dedupe:{result.dedupe_status}",
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return JobImportOut(
        imported_via=imported_via,
        posting_id=result.posting_id,
        canonical_job_id=result.canonical_job_id,
        dedupe_status=result.dedupe_status,
        created=result.created,
    )


async def _get_canonical(
    db: AsyncSession, job_id: uuid.UUID, ctx: AuthContext
) -> CanonicalJob:
    """读取岗位并执行可见性规则：他人私有岗位与不存在同样 404（不泄露存在性）。"""
    canonical = (
        await db.execute(select(CanonicalJob).where(CanonicalJob.id == job_id))
    ).scalar_one_or_none()
    if canonical is None or (
        canonical.visibility == "private" and canonical.owner_user_id != ctx.user.id
    ):
        raise AppError(code="NOT_FOUND", message="资源不存在", status_code=404)
    return canonical


@router.get("/jobs/{job_id}/sources", response_model=JobSourcesOut)
async def get_job_sources(
    job_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> JobSourcesOut:
    """去重后保留的全部来源链接（企业官网为主来源，其余不删除）。

    纵深防御：即使 canonical 可见，他人导入的 posting（个人来源记录）也绝不返回。
    """
    canonical = await _get_canonical(db, job_id, ctx)
    rows = (
        await db.execute(
            select(JobPosting, JobSource)
            .join(JobSource, JobSource.id == JobPosting.job_source_id)
            .where(
                JobPosting.canonical_job_id == canonical.id,
                or_(
                    JobPosting.imported_by_user_id.is_(None),
                    JobPosting.imported_by_user_id == ctx.user.id,
                ),
            )
            .order_by(JobPosting.first_seen_at)
        )
    ).all()
    return JobSourcesOut(
        canonical_job_id=canonical.id,
        items=[
            JobSourceLinkOut(
                posting_id=posting.id,
                source_key=source.source_key,
                source_name=source.name,
                source_type=source.source_type,
                source_url=posting.source_url,
                published_at=posting.published_at,
                first_seen_at=posting.first_seen_at,
                last_seen_at=posting.last_seen_at,
                status=posting.status,
                is_primary=(posting.id == canonical.primary_posting_id),
            )
            for posting, source in rows
        ],
    )


@router.post("/jobs/{job_id}/validity-check", response_model=ValidityCheckOut)
async def check_job_validity(
    job_id: uuid.UUID,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis_dep),
) -> ValidityCheckOut:
    """低频重新检查有效性：三态 active/inactive/unknown（登录墙/临时错误 ≠ 下架）。"""
    await enforce_rate_limit(
        redis,
        bucket=f"validity_check:{ctx.user.id}",
        limit=_VALIDITY_CHECK_LIMIT_PER_HOUR,
        window_seconds=3600,
    )
    canonical = await _get_canonical(db, job_id, ctx)

    posting: JobPosting | None = None
    if canonical.primary_posting_id is not None:
        posting = (
            await db.execute(
                select(JobPosting).where(JobPosting.id == canonical.primary_posting_id)
            )
        ).scalar_one_or_none()
    if posting is None:
        posting = (
            await db.execute(
                select(JobPosting)
                .where(JobPosting.canonical_job_id == canonical.id)
                .order_by(JobPosting.first_seen_at)
                .limit(1)
            )
        ).scalar_one_or_none()
    if posting is None:
        raise AppError(code="NOT_FOUND", message="资源不存在", status_code=404)

    source = (
        await db.execute(select(JobSource).where(JobSource.id == posting.job_source_id))
    ).scalar_one()
    adapter = get_adapter(source.source_key)

    from datetime import UTC, datetime

    result_status: ValidityStatus
    reason: str | None
    if adapter is None or not posting.source_job_id:
        # 用户导入/无自动访问方式：无法自动核实 → unknown（不伪造结论）
        result_status, checked_at, reason = (
            "unknown",
            datetime.now(UTC),
            "NO_AUTOMATED_ACCESS",
        )
    else:
        from app.jobs.adapters.base import SourceJobRef

        ref = SourceJobRef(
            source_key=source.source_key,
            source_job_id=posting.source_job_id,
            url=posting.source_url or "",
        )
        validity = await adapter.check_validity(ref)
        result_status, checked_at, reason = (
            validity.status,
            validity.checked_at,
            validity.reason,
        )

    if result_status in ("active", "inactive"):
        posting.status = result_status
        canonical.status = result_status
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="job_validity_checked",
        resource_type="canonical_job",
        resource_id=str(canonical.id),
        reason_code=result_status,
    )
    await db.commit()
    return ValidityCheckOut(
        canonical_job_id=canonical.id,
        status=result_status,
        checked_at=checked_at,
        reason=reason,
    )
