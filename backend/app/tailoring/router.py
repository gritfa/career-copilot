"""定制简历与导出 API（docs/04 第 8 节，ADR-001 裁剪版）。

- POST /recommendations/{id}/resume-drafts：创建岗位定制草稿（每日 3 个新版本/用户；
  编辑/确认/重新导出不计数）；同一推荐已有生成中的版本时幂等返回。
- GET /resume-versions、GET /resume-versions/{id}：内容 + 逐项调整明细（可追溯）。
- PATCH /resume-versions/{id}：用户编辑草稿；服务端重新验证事实引用——
  没有已确认事实支撑的条目被拒绝（新事实必须走候选确认流程）。
- POST /resume-versions/{id}/confirm：确认最终内容（再跑一次确定性校验）。
- POST /resume-versions/{id}/exports + GET /resume-exports/{id}：异步 DOCX/PDF 导出，
  限时签名下载链接；过期文件清理，不留服务器长期副本。
- 越权一律 404（防 IDOR，不泄露存在性）；日志/响应错误不含简历正文。
"""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from datetime import time as dtime

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import TypeAdapter
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import AuthContext, require_active_user, require_user
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.quotas import effective_limit
from app.db.models import (
    CanonicalJob,
    ProfileFact,
    Recommendation,
    ResumeExport,
    ResumeVersion,
    SearchPlan,
)
from app.db.session import get_db
from app.integrations.storage import get_storage
from app.resumes.parser import PROTECTED_FACT_TYPES
from app.tailoring.prompts import TAILOR_PROMPT_VERSION
from app.tailoring.schemas import (
    TEMPLATE_ID,
    ExportFormat,
    ResumeChange,
    ResumeContent,
    ResumeCreatedBy,
    ResumeExportCreateIn,
    ResumeExportOut,
    ResumeExportStatus,
    ResumeVersionKind,
    ResumeVersionListOut,
    ResumeVersionOut,
    ResumeVersionPatchIn,
    ResumeVersionStatus,
)
from app.tailoring.synthetic import get_tailor_adapter
from app.tailoring.tasks import export_resume_task, generate_resume_draft_task
from app.tailoring.validation import fact_value_text, validate_resume_content
from app.tasks.celery_app import dispatch_task

router = APIRouter(tags=["resume-tailoring"])

_NOT_FOUND = AppError(code="NOT_FOUND", message="资源不存在", status_code=404)

_MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}

# DB 存 str（有 CHECK 约束），出参 Literal：运行时真校验，非静态断言
_VERSION_KIND_ADAPTER: TypeAdapter[ResumeVersionKind] = TypeAdapter(ResumeVersionKind)
_VERSION_STATUS_ADAPTER: TypeAdapter[ResumeVersionStatus] = TypeAdapter(ResumeVersionStatus)
_CREATED_BY_ADAPTER: TypeAdapter[ResumeCreatedBy] = TypeAdapter(ResumeCreatedBy)
_EXPORT_FORMAT_ADAPTER: TypeAdapter[ExportFormat] = TypeAdapter(ExportFormat)
_EXPORT_STATUS_ADAPTER: TypeAdapter[ResumeExportStatus] = TypeAdapter(ResumeExportStatus)


def _version_out(version: ResumeVersion) -> ResumeVersionOut:
    content = None
    if version.content_json is not None:
        content = ResumeContent.model_validate(version.content_json)
    generator = version.generator_json or {}
    provider = generator.get("provider")
    return ResumeVersionOut(
        id=version.id,
        kind=_VERSION_KIND_ADAPTER.validate_python(version.kind),
        recommendation_id=version.recommendation_id,
        canonical_job_id=version.canonical_job_id,
        parent_version_id=version.parent_version_id,
        template_id=version.template_id,
        status=_VERSION_STATUS_ADAPTER.validate_python(version.status),
        created_by=_CREATED_BY_ADAPTER.validate_python(version.created_by),
        provider=provider,
        model_id=generator.get("model_id"),
        # 合成/未经真实模型验证的产出如实标注（复用阶段 6 已验证供应商清单语义）
        verified=False if provider is None else _provider_verified(provider),
        error_code=version.error_code,
        content=content,
        changes=[ResumeChange.model_validate(c) for c in (version.changes_json or [])],
        edited_by_user_at=version.edited_by_user_at,
        confirmed_at=version.confirmed_at,
        created_at=version.created_at,
    )


def _provider_verified(provider: str) -> bool:
    from app.agents.service import provider_verified

    return provider_verified(provider)


async def _get_owned_recommendation(
    db: AsyncSession, rec_id: uuid.UUID, ctx: AuthContext
) -> Recommendation:
    row = (
        await db.execute(
            select(Recommendation, SearchPlan)
            .join(SearchPlan, SearchPlan.id == Recommendation.search_plan_id)
            .join(CanonicalJob, CanonicalJob.id == Recommendation.canonical_job_id)
            .where(Recommendation.id == rec_id)
            # 岗位可见性与 /jobs、/recommendations 同一规则：
            # 岗位转私有/属主注销后，不得再基于它生成定制简历
            .where(
                or_(
                    CanonicalJob.visibility == "global",
                    CanonicalJob.owner_user_id == ctx.user.id,
                )
            )
        )
    ).first()
    if row is None or row[1].user_id != ctx.user.id:
        raise _NOT_FOUND
    return row[0]


async def _get_owned_version(
    db: AsyncSession, version_id: uuid.UUID, ctx: AuthContext
) -> ResumeVersion:
    version = (
        await db.execute(select(ResumeVersion).where(ResumeVersion.id == version_id))
    ).scalar_one_or_none()
    if version is None or version.user_id != ctx.user.id or version.status == "deleted":
        raise _NOT_FOUND
    return version


async def _load_allowed_facts(db: AsyncSession, user_id: uuid.UUID) -> dict[str, str]:
    facts = (
        (
            await db.execute(
                select(ProfileFact).where(
                    ProfileFact.user_id == user_id, ProfileFact.status == "active"
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        str(f.id): fact_value_text(f.value_json)
        for f in facts
        if f.fact_type not in PROTECTED_FACT_TYPES
    }


def _validate_or_422(content: ResumeContent, allowed_facts: dict[str, str]) -> None:
    """服务端事实引用校验：未确认事实/编造数字 → 422（不无提示写入）。"""
    violations = validate_resume_content(
        content, [], allowed_facts=allowed_facts, job_text="", allowed_spans=set()
    )
    if violations:
        raise AppError(
            code="FACT_REFERENCE_INVALID",
            message=(
                "内容包含未确认事实引用或与事实不符的数字；"
                "新技能/新经历请先走候选事实确认流程"
            ),
            status_code=422,
            # 只回传定位与违规类别，不回显内容
            details={"violations": violations[:10]},
        )


# ---------------- 生成 ----------------


@router.post(
    "/recommendations/{rec_id}/resume-drafts",
    response_model=ResumeVersionOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_resume_draft(
    rec_id: uuid.UUID,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeVersionOut:
    """针对某条推荐生成岗位定制简历草稿（每日 3 个新版本；生成中幂等复用）。"""
    rec = await _get_owned_recommendation(db, rec_id, ctx)

    # 同一推荐已有生成中的版本：幂等返回，不重复扣额度
    active = (
        await db.execute(
            select(ResumeVersion)
            .where(
                ResumeVersion.recommendation_id == rec.id,
                ResumeVersion.status == "generating",
            )
            .order_by(ResumeVersion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if active is not None:
        return _version_out(active)

    # 每日新定制版本额度（UTC 日界；编辑/确认/重新导出不计数；支持每用户覆盖）
    limit = effective_limit(ctx.user.quota_overrides_json, "resume_tailor_daily")
    day_start = datetime.combine(datetime.now(UTC).date(), dtime.min, tzinfo=UTC)
    used = (
        await db.execute(
            select(func.count())
            .select_from(ResumeVersion)
            .where(
                ResumeVersion.user_id == ctx.user.id,
                ResumeVersion.kind == "job_tailored",
                ResumeVersion.created_at >= day_start,
            )
        )
    ).scalar_one()
    if used >= limit:
        raise AppError(
            code="RATE_LIMITED",
            message="今日新建定制简历数量已用完，明天再试（编辑与导出不受限）",
            status_code=429,
            details={"limit": limit, "used": int(used)},
        )

    adapter = get_tailor_adapter()
    version = ResumeVersion(
        user_id=ctx.user.id,
        kind="job_tailored",
        search_plan_id=rec.search_plan_id,
        canonical_job_id=rec.canonical_job_id,
        recommendation_id=rec.id,
        template_id=TEMPLATE_ID,
        status="generating",
        created_by="agent_draft",
        generator_json={
            "provider": adapter.provider,
            "model_id": adapter.model_id,
            "prompt_version": TAILOR_PROMPT_VERSION,
        },
    )
    db.add(version)
    await db.commit()
    await db.refresh(version)
    dispatch_task(generate_resume_draft_task, str(version.id))
    # eager（测试）模式下任务已同步完成：返回落库后的最新状态
    await db.refresh(version)
    return _version_out(version)


# ---------------- 版本查询 / 编辑 / 确认 ----------------


@router.get("/resume-versions", response_model=ResumeVersionListOut)
async def list_resume_versions(
    recommendation_id: uuid.UUID | None = Query(default=None),
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeVersionListOut:
    """当前用户的简历版本列表（新→旧；不含已删除）。"""
    stmt = select(ResumeVersion).where(
        ResumeVersion.user_id == ctx.user.id, ResumeVersion.status != "deleted"
    )
    if recommendation_id is not None:
        stmt = stmt.where(ResumeVersion.recommendation_id == recommendation_id)
    versions = (
        (
            await db.execute(
                stmt.order_by(ResumeVersion.created_at.desc(), ResumeVersion.id.desc())
            )
        )
        .scalars()
        .all()
    )
    return ResumeVersionListOut(items=[_version_out(v) for v in versions])


@router.get("/resume-versions/{version_id}", response_model=ResumeVersionOut)
async def get_resume_version(
    version_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeVersionOut:
    """内容、事实引用与逐项调整明细。"""
    version = await _get_owned_version(db, version_id, ctx)
    return _version_out(version)


@router.patch("/resume-versions/{version_id}", response_model=ResumeVersionOut)
async def patch_resume_version(
    version_id: uuid.UUID,
    payload: ResumeVersionPatchIn,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeVersionOut:
    """编辑草稿：整体替换内容；服务端重新验证事实引用（不计每日额度）。"""
    version = await _get_owned_version(db, version_id, ctx)
    if version.status != "draft":
        raise AppError(
            code="VERSION_NOT_EDITABLE",
            message="只有草稿状态的版本可以编辑",
            status_code=409,
        )
    allowed_facts = await _load_allowed_facts(db, ctx.user.id)
    _validate_or_422(payload.content, allowed_facts)
    version.content_json = payload.content.model_dump(mode="json")
    version.edited_by_user_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(version)
    return _version_out(version)


@router.post("/resume-versions/{version_id}/confirm", response_model=ResumeVersionOut)
async def confirm_resume_version(
    version_id: uuid.UUID,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeVersionOut:
    """确认最终内容（确认前再跑一次确定性事实引用校验）。"""
    version = await _get_owned_version(db, version_id, ctx)
    if version.status == "confirmed":
        return _version_out(version)  # 幂等
    if version.status != "draft" or version.content_json is None:
        raise AppError(
            code="VERSION_NOT_CONFIRMABLE",
            message="只有生成完成的草稿可以确认",
            status_code=409,
        )
    allowed_facts = await _load_allowed_facts(db, ctx.user.id)
    _validate_or_422(ResumeContent.model_validate(version.content_json), allowed_facts)
    version.status = "confirmed"
    version.confirmed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(version)
    return _version_out(version)


# ---------------- 导出与下载 ----------------


def _download_sig(export_id: uuid.UUID, expires_epoch: int) -> str:
    secret = get_settings().secret_pepper.encode("utf-8")
    message = f"resume-export:{export_id}:{expires_epoch}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _export_out(export: ResumeExport) -> ResumeExportOut:
    download_url = None
    now = datetime.now(UTC)
    if (
        export.status == "succeeded"
        and export.expires_at is not None
        and export.expires_at > now
        and export.file_purged_at is None
    ):
        expires_epoch = int(export.expires_at.timestamp())
        sig = _download_sig(export.id, expires_epoch)
        download_url = (
            f"/resume-exports/{export.id}/download?expires={expires_epoch}&sig={sig}"
        )
    return ResumeExportOut(
        id=export.id,
        resume_version_id=export.resume_version_id,
        format=_EXPORT_FORMAT_ADAPTER.validate_python(export.format),
        status=_EXPORT_STATUS_ADAPTER.validate_python(export.status),
        error_code=export.error_code,
        size_bytes=export.size_bytes,
        file_sha256=export.file_sha256,
        download_url=download_url,
        expires_at=export.expires_at,
        created_at=export.created_at,
        completed_at=export.completed_at,
    )


async def _purge_export_file(db: AsyncSession, export: ResumeExport) -> None:
    """过期清理：删除存储对象，行保留（file_purged_at 标记），不留长期副本。"""
    if export.storage_key and export.file_purged_at is None:
        storage = get_storage()
        if storage.exists(export.storage_key):
            storage.delete(export.storage_key)
        export.file_purged_at = datetime.now(UTC)
        await db.commit()


@router.post(
    "/resume-versions/{version_id}/exports",
    response_model=ResumeExportOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_resume_export(
    version_id: uuid.UUID,
    payload: ResumeExportCreateIn,
    ctx: AuthContext = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeExportOut:
    """DOCX/PDF 异步导出（仅已确认版本；重新导出不计每日额度）。"""
    version = await _get_owned_version(db, version_id, ctx)
    if version.status != "confirmed":
        raise AppError(
            code="VERSION_NOT_CONFIRMED",
            message="请先确认简历内容，再导出文件",
            status_code=409,
        )
    export = ResumeExport(
        resume_version_id=version.id,
        user_id=ctx.user.id,
        format=payload.format,
        status="queued",
    )
    db.add(export)
    await db.commit()
    await db.refresh(export)
    dispatch_task(export_resume_task, str(export.id))
    await db.refresh(export)
    return _export_out(export)


@router.get("/resume-exports/{export_id}", response_model=ResumeExportOut)
async def get_resume_export(
    export_id: uuid.UUID,
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeExportOut:
    """导出状态与限时下载链接（过期链接不再返回，文件顺带清理）。"""
    export = (
        await db.execute(select(ResumeExport).where(ResumeExport.id == export_id))
    ).scalar_one_or_none()
    if export is None or export.user_id != ctx.user.id:
        raise _NOT_FOUND
    if export.expires_at is not None and export.expires_at <= datetime.now(UTC):
        await _purge_export_file(db, export)
    return _export_out(export)


@router.get("/resume-exports/{export_id}/download")
async def download_resume_export(
    export_id: uuid.UUID,
    expires: int = Query(),
    sig: str = Query(min_length=64, max_length=64),
    ctx: AuthContext = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """限时签名下载：越权、签名不符、过期或文件已清理一律拒绝。"""
    export = (
        await db.execute(select(ResumeExport).where(ResumeExport.id == export_id))
    ).scalar_one_or_none()
    if export is None or export.user_id != ctx.user.id or export.status != "succeeded":
        raise _NOT_FOUND
    if not hmac.compare_digest(sig, _download_sig(export.id, expires)):
        raise _NOT_FOUND  # 签名不符不泄露更多信息
    now = datetime.now(UTC)
    if (
        datetime.fromtimestamp(expires, tz=UTC) <= now
        or export.expires_at is None
        or export.expires_at <= now
        or export.file_purged_at is not None
    ):
        await _purge_export_file(db, export)
        raise AppError(
            code="EXPORT_EXPIRED",
            message="下载链接已过期，文件已清理；请重新导出",
            status_code=410,
        )
    storage = get_storage()
    if not export.storage_key or not storage.exists(export.storage_key):
        raise _NOT_FOUND
    data = storage.open(export.storage_key)
    # 文件名只含版本短 ID（无姓名/邮箱等 PII）
    filename = f"resume_{export.resume_version_id.hex[:8]}.{export.format}"
    return Response(
        content=data,
        media_type=_MEDIA_TYPES[export.format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
