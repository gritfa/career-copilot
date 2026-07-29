"""定制简历生成与导出执行（同步，供 Celery 任务调用）。

流程：
- 生成：generating →（构造输入 + 走 Model Gateway）→ 确定性事实引用校验
  → draft / failed。校验失败/模型失败 → 明确 failed，绝不落半成品内容。
- 导出：queued → running →（确认版本 + 导出前再跑一次确定性引用校验）
  → 渲染 DOCX/PDF → succeeded / failed；文件短时保留，过期清理。

红线：
- 真实供应商调用前必须有 provider+profile_fields 授权（复用阶段 6 门控）；
- 未确认事实/虚构数字绝不进内容（validation 强制）；
- 日志只含 ID/计数/错误码，绝不含事实内容、岗位正文或简历正文。
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.service import ANALYSIS_CONSENT_SCOPE, check_consent_sync
from app.core.config import get_settings
from app.db.models import (
    CanonicalJob,
    Company,
    JobPosting,
    MatchComponent,
    ProfileFact,
    Recommendation,
    ResumeExport,
    ResumeVersion,
    UsageLedger,
)
from app.integrations.llm_gateway import (
    PROVIDERS_REQUIRING_CONSENT,
    ConsentDecision,
    LLMAuthorizationError,
    LLMError,
    LLMNotConfiguredError,
    LLMSchemaError,
    LLMUsage,
    ModelGateway,
)
from app.integrations.storage import get_storage
from app.resumes.parser import PROTECTED_FACT_TYPES
from app.tailoring.prompts import (
    TAILOR_PROMPT_VERSION,
    build_tailor_input,
    build_tailor_request,
)
from app.tailoring.render import render_resume
from app.tailoring.schemas import ResumeContent, TailoredResumeDraft
from app.tailoring.synthetic import get_tailor_adapter
from app.tailoring.validation import fact_value_text, validate_resume_content

logger = structlog.get_logger("app.tailoring.service")


def load_allowed_facts(db: Session, user_id: uuid.UUID) -> dict[str, str]:
    """允许引用的事实集合：active 已确认事实（受保护属性类型除外）。

    值为事实原值的展平文本，用于数字一致性校验。
    """
    facts = (
        db.execute(
            select(ProfileFact).where(
                ProfileFact.user_id == user_id, ProfileFact.status == "active"
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


def _fail_version(db: Session, version: ResumeVersion, error_code: str) -> dict:
    version.status = "failed"
    version.error_code = error_code
    version.content_json = None
    db.commit()
    logger.warning(
        "resume_tailor_failed",
        version_id=str(version.id),
        recommendation_id=str(version.recommendation_id),
        error_code=error_code,
    )
    return {"status": "failed", "error_code": error_code}


def _record_llm_usage(db: Session, user_id: uuid.UUID, usage: LLMUsage) -> None:
    db.add(
        UsageLedger(
            user_id=user_id,
            provider=usage.provider,
            model=usage.model_id,
            operation_type="llm_tailor",
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            amount_estimated=usage.amount_estimated,
        )
    )


def execute_resume_tailoring(
    db: Session, version_id: uuid.UUID, gateway: ModelGateway | None = None
) -> dict:
    """执行一次定制简历生成（幂等：非 generating 状态直接返回）。"""
    version = db.get(ResumeVersion, version_id)
    if version is None:
        return {"status": "version_not_found"}
    if version.status != "generating":
        return {"status": f"skipped:{version.status}"}

    rec = (
        db.get(Recommendation, version.recommendation_id)
        if version.recommendation_id
        else None
    )
    if rec is None:
        return _fail_version(db, version, "RECOMMENDATION_NOT_FOUND")

    job = db.get(CanonicalJob, rec.canonical_job_id)
    posting = None
    if job is not None and job.primary_posting_id is not None:
        posting = db.get(JobPosting, job.primary_posting_id)
    if posting is None and job is not None:
        posting = db.execute(
            select(JobPosting)
            .where(JobPosting.canonical_job_id == job.id)
            .order_by(JobPosting.first_seen_at)
            .limit(1)
        ).scalar_one_or_none()
    if posting is None:
        return _fail_version(db, version, "JOB_POSTING_NOT_FOUND")
    company = db.get(Company, posting.company_id) if posting.company_id else None
    components = (
        db.execute(
            select(MatchComponent).where(MatchComponent.recommendation_id == rec.id)
        )
        .scalars()
        .all()
    )
    facts = (
        db.execute(
            select(ProfileFact).where(
                ProfileFact.user_id == version.user_id, ProfileFact.status == "active"
            )
        )
        .scalars()
        .all()
    )
    facts = [f for f in facts if f.fact_type not in PROTECTED_FACT_TYPES]
    if not facts:
        return _fail_version(db, version, "NO_CONFIRMED_FACTS")

    gateway = gateway or ModelGateway(adapter=get_tailor_adapter())
    version.generator_json = {
        "provider": gateway.adapter.provider,
        "model_id": gateway.adapter.model_id,
        "prompt_version": TAILOR_PROMPT_VERSION,
    }
    db.commit()

    # ---- 授权门控：真实供应商必须有独立授权；合成实现不出本机 ----
    consent: ConsentDecision | None = None
    if gateway.adapter.provider in PROVIDERS_REQUIRING_CONSENT:
        consent = check_consent_sync(
            db, version.user_id, gateway.adapter.provider, ANALYSIS_CONSENT_SCOPE
        )
        if not consent.allowed:
            return _fail_version(db, version, "CONSENT_REQUIRED")

    input_doc = build_tailor_input(
        facts=facts,
        posting=posting,
        job_title=posting.title_raw or "",
        company_name=company.canonical_name if company else "",
        recommendation=rec,
        components=components,
    )
    try:
        draft, usage = gateway.complete_json(
            build_tailor_request(input_doc), TailoredResumeDraft, consent=consent
        )
    except LLMSchemaError:
        return _fail_version(db, version, "SCHEMA_INVALID")
    except (LLMNotConfiguredError, LLMAuthorizationError):
        return _fail_version(db, version, "CONSENT_REQUIRED")
    except LLMError:
        return _fail_version(db, version, "MODEL_UNAVAILABLE")

    _record_llm_usage(db, version.user_id, usage)

    # ---- 确定性校验：未确认事实引用/编造数字/编造原文 → 明确失败 ----
    allowed_facts = {str(f.id): fact_value_text(f.value_json) for f in facts}
    job_text = (
        f"{posting.title_raw or ''}\n{posting.description_text or ''}\n"
        f"{posting.salary_raw or ''}"
    )
    allowed_spans: set[str] = set()
    for comp in components:
        for ref in comp.evidence_refs_json or []:
            span = str((ref.get("job_evidence") or {}).get("span", ""))
            if span:
                allowed_spans.add(span)
    for item in rec.hard_conditions_json or []:
        evidence = str(item.get("evidence", ""))
        if evidence:
            allowed_spans.add(evidence)
    violations = validate_resume_content(
        draft.content,
        draft.changes,
        allowed_facts=allowed_facts,
        job_text=job_text,
        allowed_spans=allowed_spans,
    )
    if violations:
        logger.warning(
            "resume_tailor_evidence_violations",
            version_id=str(version.id),
            violation_count=len(violations),
            kinds=sorted({v.split(":", 1)[1] for v in violations})[:5],
        )
        return _fail_version(db, version, "EVIDENCE_VALIDATION_FAILED")

    version.status = "draft"
    version.error_code = None
    version.content_json = draft.content.model_dump(mode="json")
    version.changes_json = [c.model_dump(mode="json") for c in draft.changes]
    db.commit()
    logger.info(
        "resume_tailor_completed",
        version_id=str(version.id),
        recommendation_id=str(version.recommendation_id),
        provider=usage.provider,
        model_id=usage.model_id,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        sections=len(draft.content.sections),
        changes=len(draft.changes),
    )
    return {"status": "draft", "version_id": str(version.id)}


# ---------------- 导出 ----------------


def _fail_export(db: Session, export: ResumeExport, error_code: str) -> dict:
    export.status = "failed"
    export.error_code = error_code
    export.storage_key = None
    export.completed_at = datetime.now(UTC)
    db.commit()
    logger.warning(
        "resume_export_failed",
        export_id=str(export.id),
        version_id=str(export.resume_version_id),
        error_code=error_code,
    )
    return {"status": "failed", "error_code": error_code}


def execute_resume_export(db: Session, export_id: uuid.UUID) -> dict:
    """执行一次 DOCX/PDF 导出（幂等：非 queued 状态直接返回）。"""
    export = db.get(ResumeExport, export_id)
    if export is None:
        return {"status": "export_not_found"}
    if export.status != "queued":
        return {"status": f"skipped:{export.status}"}

    export.status = "running"
    db.commit()

    version = db.get(ResumeVersion, export.resume_version_id)
    if version is None or version.status == "deleted":
        return _fail_export(db, export, "VERSION_NOT_FOUND")
    if version.status != "confirmed" or version.content_json is None:
        return _fail_export(db, export, "VERSION_NOT_CONFIRMED")

    # ---- 导出前确定性事实引用校验（确认后事实被撤销/废止 → 阻止导出） ----
    content = ResumeContent.model_validate(version.content_json)
    allowed_facts = load_allowed_facts(db, version.user_id)
    violations = validate_resume_content(
        content, [], allowed_facts=allowed_facts, job_text="", allowed_spans=set()
    )
    if violations:
        logger.warning(
            "resume_export_reference_check_failed",
            export_id=str(export.id),
            violation_count=len(violations),
        )
        return _fail_export(db, export, "FACT_REFERENCE_INVALID")

    try:
        data = render_resume(content, export.format)
    except Exception:  # 渲染库异常不外传细节
        logger.error("resume_export_render_error", export_id=str(export.id))
        return _fail_export(db, export, "RENDER_FAILED")

    storage_key = f"resume-exports/{export.id.hex}.{export.format}"
    get_storage().save(storage_key, data)
    now = datetime.now(UTC)
    export.storage_key = storage_key
    export.file_sha256 = hashlib.sha256(data).hexdigest()
    export.size_bytes = len(data)
    export.expires_at = now + timedelta(seconds=get_settings().resume_export_ttl_seconds)
    export.completed_at = now
    export.status = "succeeded"
    db.commit()
    logger.info(
        "resume_export_completed",
        export_id=str(export.id),
        version_id=str(export.resume_version_id),
        format=export.format,
        size_bytes=export.size_bytes,
    )
    return {"status": "succeeded", "export_id": str(export.id)}
