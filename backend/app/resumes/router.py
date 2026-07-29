"""简历上传/列表/删除 + 解析结果 + 候选确认（docs/04 第 4 节）。

安全边界：
- 上传前置条件：已登录、非 deletion_pending（require_active_user）、age_attested。
- 三重文件校验 + 恶意扫描接口（未配置引擎时如实标 skipped_not_configured）。
- 越权访问他人简历一律 404；日志/审计不含文件名、正文、联系方式。
- 未确认候选绝不进入 profile_facts；rejected 不入库。
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query, Request, UploadFile, status
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_audit
from app.auth.deps import AuthContext, ensure_owner, require_active_user, require_user
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.redis import get_redis_dep
from app.core.security import hash_ip
from app.db.models import FactCandidate, FactEvidence, ProfileFact, Resume, ResumeParse
from app.db.session import get_db
from app.integrations.malware import get_malware_scanner
from app.integrations.storage import get_storage
from app.resumes.parser import PARSER_NAME, PARSER_VERSION, PROTECTED_FACT_TYPES
from app.resumes.schemas import (
    CandidateOut,
    FactsConfirmOut,
    FactsConfirmRequest,
    ParseOut,
    ProfileFactOut,
    ResumeCreateOut,
    ResumeCreateRequest,
    ResumeDeleteOut,
    ResumeListOut,
    ResumeOut,
    UploadSessionOut,
)
from app.resumes.tasks import cleanup_resume_task, parse_resume_task
from app.resumes.validation import validate_upload
from app.tasks.celery_app import dispatch_task

router = APIRouter(tags=["resumes"])

_UPLOAD_KEY_PREFIX = "resume_upload:"


def _ensure_age_attested(ctx: AuthContext) -> None:
    """上传前置条件：18 岁声明（docs/01 目标人群硬约束）。"""
    if ctx.user.age_attested_at is None:
        raise AppError(
            code="AGE_ATTESTATION_REQUIRED",
            message="需要先完成年龄声明（18 周岁以上）才能上传简历",
            status_code=403,
        )


def _resume_out(resume: Resume, latest_parse_status: str | None = None) -> ResumeOut:
    return ResumeOut(
        id=resume.id,
        original_filename=resume.original_filename,
        media_type=resume.media_type,
        size_bytes=resume.size_bytes,
        sha256=resume.sha256,
        page_count=resume.page_count,
        status=resume.status,
        malware_scan_status=resume.malware_scan_status,
        text_extract_status=resume.text_extract_status,
        uploaded_at=resume.uploaded_at,
        latest_parse_status=latest_parse_status,
    )


async def _get_owned_resume(
    db: AsyncSession, resume_id: uuid.UUID, ctx: AuthContext, include_deleted: bool = False
) -> Resume:
    resume = (
        await db.execute(select(Resume).where(Resume.id == resume_id))
    ).scalar_one_or_none()
    ensure_owner(resume.user_id if resume else None, ctx.user)
    assert resume is not None
    if resume.status == "deleted" and not include_deleted:
        raise AppError(code="NOT_FOUND", message="资源不存在", status_code=404)
    return resume


# ---------------- 上传 ----------------


@router.post(
    "/resumes/uploads", response_model=UploadSessionOut, status_code=status.HTTP_201_CREATED
)
async def create_upload_session(
    file: UploadFile,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis_dep),
) -> UploadSessionOut:
    """上传会话：本地存储直接收文件（OSS 预签名留到真实接入）。

    通过全部校验后文件落存储、元数据进 Redis（带 TTL），
    再由 POST /resumes 确认并创建解析任务。
    """
    _ensure_age_attested(ctx)
    settings = get_settings()

    data = await file.read(settings.resume_max_size_bytes + 1)
    validated = validate_upload(file.filename or "", file.content_type, data)

    scan_status = get_malware_scanner().scan(data)
    if scan_status == "infected":
        await record_audit(
            db,
            actor_type="user",
            actor_id=ctx.user.id,
            action="resume_upload_rejected",
            result="denied",
            reason_code="MALWARE_DETECTED",
            ip_hash=hash_ip(request.client.host if request.client else None),
        )
        await db.commit()
        raise AppError(code="MALWARE_DETECTED", message="文件未通过安全扫描", status_code=422)

    sha256 = hashlib.sha256(data).hexdigest()
    upload_id = uuid.uuid4().hex
    # key 只含 UUID：不含邮箱/原文件名（docs/08 第 5 节）
    storage_key = f"resumes/{ctx.user.id}/{uuid.uuid4().hex}{validated.extension}"
    get_storage().save(storage_key, data)

    session_payload = {
        "user_id": str(ctx.user.id),
        "storage_key": storage_key,
        "original_filename": (file.filename or "")[:255],
        "media_type": validated.media_type,
        "size_bytes": validated.size_bytes,
        "sha256": sha256,
        "page_count": validated.page_count,
        "malware_scan_status": scan_status,
    }
    await redis.set(
        f"{_UPLOAD_KEY_PREFIX}{upload_id}",
        json.dumps(session_payload),
        ex=settings.upload_session_ttl_seconds,
    )

    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="resume_upload_session_created",
        resource_type="resume_upload",
        resource_id=upload_id,
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()

    return UploadSessionOut(
        upload_id=upload_id,
        sha256=sha256,
        size_bytes=validated.size_bytes,
        media_type=validated.media_type,
        page_count=validated.page_count,
        malware_scan_status=scan_status,
        expires_in_seconds=settings.upload_session_ttl_seconds,
    )


@router.post("/resumes", response_model=ResumeCreateOut, status_code=status.HTTP_202_ACCEPTED)
async def confirm_upload(
    payload: ResumeCreateRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis_dep),
) -> ResumeCreateOut:
    """上传完成确认：创建简历记录 + 幂等解析任务（202）。"""
    _ensure_age_attested(ctx)

    session_key = f"{_UPLOAD_KEY_PREFIX}{payload.upload_id}"
    raw = await redis.get(session_key)
    if raw is None:
        raise AppError(
            code="UPLOAD_SESSION_NOT_FOUND",
            message="上传会话不存在或已过期，请重新上传",
            status_code=404,
        )
    session = json.loads(raw)
    if session["user_id"] != str(ctx.user.id):
        # 不泄露他人会话存在性
        raise AppError(
            code="UPLOAD_SESSION_NOT_FOUND",
            message="上传会话不存在或已过期，请重新上传",
            status_code=404,
        )

    # 同文件去重（sha256）：直接返回已存在的简历，不重复入库/解析
    existing = (
        await db.execute(
            select(Resume).where(
                Resume.user_id == ctx.user.id,
                Resume.sha256 == session["sha256"],
                Resume.status.notin_(("deleting", "deleted")),
            )
        )
    ).scalars().first()
    if existing is not None:
        get_storage().delete(session["storage_key"])  # 丢弃重复副本
        await redis.delete(session_key)
        latest = (
            await db.execute(
                select(ResumeParse.status)
                .where(ResumeParse.resume_id == existing.id)
                .order_by(ResumeParse.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return ResumeCreateOut(
            resume=_resume_out(existing, latest), parse_id=None, duplicate=True
        )

    idempotency_key = f"{ctx.user.id}:{session['sha256']}:{PARSER_VERSION}"
    key_taken = (
        await db.execute(
            select(ResumeParse.id).where(ResumeParse.idempotency_key == idempotency_key)
        )
    ).first()
    if key_taken is not None:
        # 同文件旧简历仍在异步删除中：等清理完成后再上传
        raise AppError(
            code="RESUME_DELETION_IN_PROGRESS",
            message="同一文件的旧简历正在删除中，请稍后重试",
            status_code=409,
        )

    resume = Resume(
        user_id=ctx.user.id,
        storage_key=session["storage_key"],
        original_filename=session["original_filename"] or "resume" ,
        media_type=session["media_type"],
        size_bytes=session["size_bytes"],
        sha256=session["sha256"],
        page_count=session["page_count"],
        status="uploaded",
        malware_scan_status=session["malware_scan_status"],
        text_extract_status="pending",
    )
    db.add(resume)
    await db.flush()

    parse = ResumeParse(
        resume_id=resume.id,
        parser_name=PARSER_NAME,
        parser_version=PARSER_VERSION,
        schema_version="1",
        status="queued",
        idempotency_key=idempotency_key,
    )
    db.add(parse)
    await db.flush()

    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="resume_created",
        resource_type="resume",
        resource_id=str(resume.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    await redis.delete(session_key)

    dispatch_task(parse_resume_task, str(parse.id))

    # eager 模式下任务已同步改库，刷新以返回最新状态
    await db.refresh(resume)
    await db.refresh(parse)
    return ResumeCreateOut(
        resume=_resume_out(resume, parse.status), parse_id=parse.id, duplicate=False
    )


# ---------------- 列表 / 详情 / 删除 ----------------


@router.get("/resumes", response_model=ResumeListOut)
async def list_resumes(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeListOut:
    """当前用户简历列表（游标分页，不返回正文/存储 key）。"""
    query = (
        select(Resume)
        .where(Resume.user_id == ctx.user.id, Resume.status != "deleted")
        .order_by(Resume.uploaded_at.desc(), Resume.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        try:
            cursor_id = uuid.UUID(cursor)
        except ValueError as exc:
            raise AppError(
                code="INVALID_CURSOR", message="无效的分页游标", status_code=400
            ) from exc
        anchor = (
            await db.execute(
                select(Resume).where(Resume.id == cursor_id, Resume.user_id == ctx.user.id)
            )
        ).scalar_one_or_none()
        if anchor is None:
            raise AppError(code="INVALID_CURSOR", message="无效的分页游标", status_code=400)
        query = (
            select(Resume)
            .where(
                Resume.user_id == ctx.user.id,
                Resume.status != "deleted",
                tuple_(Resume.uploaded_at, Resume.id) < (anchor.uploaded_at, anchor.id),
            )
            .order_by(Resume.uploaded_at.desc(), Resume.id.desc())
            .limit(limit + 1)
        )

    rows = (await db.execute(query)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    return ResumeListOut(
        items=[_resume_out(r) for r in rows],
        next_cursor=str(rows[-1].id) if has_more and rows else None,
    )


@router.get("/resumes/{resume_id}", response_model=ResumeOut)
async def get_resume(
    resume_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeOut:
    resume = await _get_owned_resume(db, resume_id, ctx)
    latest = (
        await db.execute(
            select(ResumeParse.status)
            .where(ResumeParse.resume_id == resume.id)
            .order_by(ResumeParse.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return _resume_out(resume, latest)


@router.delete("/resumes/{resume_id}", response_model=ResumeDeleteOut, status_code=202)
async def delete_resume(
    resume_id: uuid.UUID,
    request: Request,
    confirm: bool = Query(default=False),
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeDeleteOut:
    """删除单份简历：依赖预检查 → 确认 → 异步清理。

    已确认 profile_facts 保留，其证据与该简历脱链（保留哈希与最小摘录）。
    """
    resume = await _get_owned_resume(db, resume_id, ctx)

    facts_referencing = (
        await db.execute(
            select(func.count(func.distinct(FactEvidence.profile_fact_id)))
            .select_from(FactEvidence)
            .join(ProfileFact, ProfileFact.id == FactEvidence.profile_fact_id)
            .where(FactEvidence.resume_id == resume.id, ProfileFact.status == "active")
        )
    ).scalar_one()
    dependencies = {"active_profile_facts_with_evidence": int(facts_referencing)}

    if not confirm:
        raise AppError(
            code="CONFIRMATION_REQUIRED",
            message="删除简历需要确认（confirm=true）；已确认事实将保留但证据会与文件脱链",
            status_code=409,
            details=dependencies,
        )

    if resume.status != "deleting":
        resume.status = "deleting"
    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="resume_delete_requested",
        resource_type="resume",
        resource_id=str(resume.id),
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()

    dispatch_task(cleanup_resume_task, str(resume.id))
    await db.refresh(resume)
    return ResumeDeleteOut(id=resume.id, status=resume.status, dependencies=dependencies)


# ---------------- 解析结果与候选确认 ----------------


@router.get("/resumes/{resume_id}/parse", response_model=ParseOut)
async def get_parse(
    resume_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ParseOut:
    """最近一次解析的状态与候选事实。"""
    resume = await _get_owned_resume(db, resume_id, ctx)
    parse = (
        await db.execute(
            select(ResumeParse)
            .where(ResumeParse.resume_id == resume.id)
            .order_by(ResumeParse.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if parse is None:
        raise AppError(code="PARSE_NOT_FOUND", message="该简历还没有解析记录", status_code=404)

    candidates = (
        (
            await db.execute(
                select(FactCandidate)
                .where(FactCandidate.resume_parse_id == parse.id)
                .order_by(FactCandidate.source_span_start)
            )
        )
        .scalars()
        .all()
    )
    return ParseOut(
        id=parse.id,
        status=parse.status,
        error_code=parse.error_code,
        parser_name=parse.parser_name,
        parser_version=parse.parser_version,
        schema_version=parse.schema_version,
        protected_discarded_count=parse.protected_discarded_count,
        started_at=parse.started_at,
        completed_at=parse.completed_at,
        candidates=[
            CandidateOut(
                id=c.id,
                fact_type=c.fact_type,
                value_json=c.value_json,
                source_span_start=c.source_span_start,
                source_span_end=c.source_span_end,
                confidence=c.confidence,
                status=c.status,
            )
            for c in candidates
        ],
    )


def _fact_out(fact: ProfileFact) -> ProfileFactOut:
    return ProfileFactOut(
        id=fact.id,
        fact_type=fact.fact_type,
        value_json=fact.value_json,
        status=fact.status,
        provenance_type=fact.provenance_type,
        confirmed_by_user_at=fact.confirmed_by_user_at,
        superseded_by_id=fact.superseded_by_id,
        created_at=fact.created_at,
    )


@router.post("/resumes/{resume_id}/facts/confirm", response_model=FactsConfirmOut)
async def confirm_facts(
    resume_id: uuid.UUID,
    payload: FactsConfirmRequest,
    request: Request,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> FactsConfirmOut:
    """批量确认候选：accept/edit → profile_facts + fact_evidence；reject 不入库。"""
    resume = await _get_owned_resume(db, resume_id, ctx)

    seen_ids = {d.candidate_id for d in payload.decisions}
    if len(seen_ids) != len(payload.decisions):
        raise AppError(code="DUPLICATE_DECISION", message="同一候选出现多次决策", status_code=400)

    rows = (
        await db.execute(
            select(FactCandidate, ResumeParse)
            .join(ResumeParse, ResumeParse.id == FactCandidate.resume_parse_id)
            .where(FactCandidate.id.in_(seen_ids), ResumeParse.resume_id == resume.id)
        )
    ).all()
    by_id = {c.id: (c, p) for c, p in rows}
    if len(by_id) != len(seen_ids):
        # 含不存在或不属于该简历的候选：一律 404，不泄露他人资源
        raise AppError(code="NOT_FOUND", message="资源不存在", status_code=404)

    # 提取文本缓存（构造最小必要证据摘录）
    text_cache: dict[uuid.UUID, str] = {}

    def _excerpt(parse: ResumeParse, start: int, end: int) -> str:
        if parse.id not in text_cache:
            if not parse.extracted_text_storage_key:
                return ""
            try:
                text_cache[parse.id] = get_storage().open(
                    parse.extracted_text_storage_key
                ).decode("utf-8")
            except FileNotFoundError:
                return ""
        return text_cache[parse.id][start:end][:200]

    now = datetime.now(UTC)
    accepted = edited = rejected = 0
    facts: list[ProfileFact] = []

    for decision in payload.decisions:
        candidate, parse = by_id[decision.candidate_id]
        if candidate.status != "pending":
            raise AppError(
                code="CANDIDATE_ALREADY_DECIDED",
                message="候选事实已被处理，不能重复决策",
                status_code=409,
                details={"candidate_id": str(candidate.id)},
            )
        if candidate.fact_type in PROTECTED_FACT_TYPES:
            # 防御性兜底：受保护属性不允许确认入库
            raise AppError(
                code="PROTECTED_ATTRIBUTE",
                message="受保护属性不能进入事实库",
                status_code=400,
            )

        if decision.action == "reject":
            candidate.status = "rejected"
            rejected += 1
            continue

        if decision.action == "edit":
            if not decision.value_json:
                raise AppError(
                    code="EDIT_VALUE_REQUIRED",
                    message="编辑接受必须提供修改后的值",
                    status_code=400,
                )
            value = decision.value_json
            candidate.status = "edited"
            edited += 1
        else:
            value = candidate.value_json
            candidate.status = "accepted"
            accepted += 1

        fact = ProfileFact(
            user_id=ctx.user.id,
            fact_type=candidate.fact_type,
            value_json=value,
            status="active",
            provenance_type="resume",
            confirmed_by_user_at=now,
        )
        db.add(fact)
        await db.flush()
        db.add(
            FactEvidence(
                profile_fact_id=fact.id,
                resume_id=resume.id,
                source_locator={
                    "resume_parse_id": str(parse.id),
                    "span_start": candidate.source_span_start,
                    "span_end": candidate.source_span_end,
                },
                evidence_hash=candidate.source_quote_hash,
                display_excerpt=_excerpt(
                    parse, candidate.source_span_start, candidate.source_span_end
                ),
            )
        )
        facts.append(fact)

    await record_audit(
        db,
        actor_type="user",
        actor_id=ctx.user.id,
        action="resume_facts_confirmed",
        resource_type="resume",
        resource_id=str(resume.id),
        reason_code=f"accepted:{accepted};edited:{edited};rejected:{rejected}",
        ip_hash=hash_ip(request.client.host if request.client else None),
    )
    await db.commit()
    return FactsConfirmOut(
        accepted=accepted,
        edited=edited,
        rejected=rejected,
        facts=[_fact_out(f) for f in facts],
    )
