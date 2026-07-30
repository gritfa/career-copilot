"""单模型标准分析执行（同步，供 Celery 任务调用）。

流程（用户可见状态：排队/分析/校验/完成/失败，docs/07 第 8.4 节）：
queued → analyzing（构造输入 + 走 Model Gateway）→ validating（确定性证据校验）
→ completed / failed。

红线：
- 真实供应商（deepseek/qwen）调用前必须有该 provider+scope 的有效授权；
  合成实现不出本机，不需要授权。
- 校验失败/模型失败 → 明确 failed（带 error_code），绝不落半成品报告；
  规则 + 向量基础匹配结果不受影响（降级语义，docs/07 第 12 节）。
- 日志只含 ID/计数/错误码，绝不含事实内容、岗位正文或报告正文。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.prompts import (
    PROMPT_VERSION,
    build_analysis_input,
    build_analysis_request,
    input_fingerprint,
)
from app.agents.schemas import (
    StandardAnalysisReport,
)
from app.db.models import (
    AgentRun,
    CanonicalJob,
    Consent,
    JobPosting,
    MatchComponent,
    ProfileFact,
    Recommendation,
    SearchPlan,
    UsageLedger,
    User,
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

logger = structlog.get_logger("app.agents.service")

# 标准分析输入为结构化已确认事实 → profile_fields 范围（docs/08 第 3 节；
# full_resume 原文不在本流程中发送）
ANALYSIS_CONSENT_SCOPE = "profile_fields"


def check_consent_sync(
    db: Session, user_id: uuid.UUID, provider: str, scope: str
) -> ConsentDecision:
    """ModelAuthorizationGuard 的同步镜像（Celery 任务用；语义与 guard 一致）。

    provider + scope 精确匹配；撤回/过期/注销中一律拒绝。
    """
    user = db.get(User, user_id)
    if user is None:
        return ConsentDecision(allowed=False, reason_code="USER_NOT_FOUND")
    if user.status == "deletion_pending":
        return ConsentDecision(allowed=False, reason_code="ACCOUNT_DELETION_PENDING")
    consents = (
        db.execute(
            select(Consent).where(
                Consent.user_id == user_id,
                Consent.provider == provider,
                Consent.scope == scope,
            )
        )
        .scalars()
        .all()
    )
    if not consents:
        return ConsentDecision(allowed=False, reason_code="NO_CONSENT")
    now = datetime.now(UTC)
    for consent in consents:
        if consent.revoked_at is not None:
            continue
        if consent.expires_at is not None and consent.expires_at <= now:
            continue
        return ConsentDecision(allowed=True)
    any_revoked = any(c.revoked_at is not None for c in consents)
    return ConsentDecision(
        allowed=False,
        reason_code="CONSENT_REVOKED" if any_revoked else "CONSENT_EXPIRED",
    )


def validate_report_evidence(
    report: StandardAnalysisReport, input_doc: dict[str, Any]
) -> list[str]:
    """确定性证据校验（validating 阶段）：返回违规项列表，空表为通过。

    - 事实引用必须 ⊆ 输入文档中的 fact_id（防虚构事实引用）；
    - job_span 必须逐字来自岗位原文或输入证据片段（防编造"原文"）；
    - 无证据的结论必须显式 uncertainty。
    """
    violations: list[str] = []
    known_fact_ids = {str(f.get("fact_id")) for f in input_doc.get("facts", [])}
    job = input_doc.get("job", {}) or {}
    job_text = f"{job.get('title', '')}\n{job.get('description', '')}\n{job.get('salary_raw', '')}"
    allowed_spans: set[str] = set()
    for comp in (input_doc.get("rule_score", {}) or {}).get("components", []) or []:
        for ref in comp.get("evidence_refs", []) or []:
            span = str((ref.get("job_evidence") or {}).get("span", ""))
            if span:
                allowed_spans.add(span)
    for item in input_doc.get("hard_conditions", []) or []:
        evidence = str(item.get("evidence", ""))
        if evidence:
            allowed_spans.add(evidence)
    for signal in input_doc.get("risk_signals", []) or []:
        evidence = str(signal.get("evidence", ""))
        if evidence:
            allowed_spans.add(evidence)

    def _span_ok(span: str) -> bool:
        return not span or span in job_text or span in allowed_spans

    for i, claim in enumerate(report.strengths):
        unknown = [fid for fid in claim.profile_fact_ids if fid not in known_fact_ids]
        if unknown:
            violations.append(f"strengths[{i}]:unknown_fact_ids")
        if not claim.profile_fact_ids and claim.uncertainty is None:
            violations.append(f"strengths[{i}]:claim_without_evidence")
        if not _span_ok(claim.job_span):
            violations.append(f"strengths[{i}]:fabricated_job_span")
    for i, gap in enumerate(report.gaps):
        if not _span_ok(gap.job_span):
            violations.append(f"gaps[{i}]:fabricated_job_span")
        if not gap.job_span and gap.uncertainty is None:
            violations.append(f"gaps[{i}]:gap_without_evidence")
    for i, suggestion in enumerate(report.resume_suggestions):
        unknown = [
            fid for fid in suggestion.based_on_fact_ids if fid not in known_fact_ids
        ]
        if unknown:
            violations.append(f"resume_suggestions[{i}]:unknown_fact_ids")
        if not suggestion.based_on_fact_ids and suggestion.uncertainty is None:
            violations.append(f"resume_suggestions[{i}]:suggestion_without_evidence")
    known_risk_codes = {
        str(s.get("code", "")) for s in input_doc.get("risk_signals", []) or []
    }
    for i, risk in enumerate(report.risks):
        if risk.code not in known_risk_codes:
            violations.append(f"risks[{i}]:unknown_risk_code")
    return violations


def _record_llm_usage(db: Session, user_id: uuid.UUID, usage: LLMUsage) -> None:
    db.add(
        UsageLedger(
            user_id=user_id,
            provider=usage.provider,
            model=usage.model_id,
            operation_type="llm_analysis",
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            amount_estimated=usage.amount_estimated,
        )
    )


def _fail(db: Session, run: AgentRun, error_code: str) -> dict:
    run.status = "failed"
    run.current_stage = None
    run.error_code = error_code
    run.completed_at = datetime.now(UTC)
    db.commit()
    logger.warning(
        "analysis_run_failed",
        run_id=str(run.id),
        recommendation_id=str(run.recommendation_id),
        error_code=error_code,
    )
    return {"status": "failed", "error_code": error_code}


def execute_standard_analysis(
    db: Session, run_id: uuid.UUID, gateway: ModelGateway | None = None
) -> dict:
    """执行一次标准分析（幂等：非 queued 状态直接返回，不重复执行）。"""
    run = db.get(AgentRun, run_id)
    if run is None:
        return {"status": "run_not_found"}
    if run.status != "queued":
        return {"status": f"skipped:{run.status}"}

    rec = db.get(Recommendation, run.recommendation_id)
    plan = db.get(SearchPlan, rec.search_plan_id) if rec else None
    if rec is None or plan is None:
        return _fail(db, run, "RECOMMENDATION_NOT_FOUND")

    gateway = gateway or ModelGateway()
    run.status = "analyzing"
    run.current_stage = "analyzing"
    run.started_at = datetime.now(UTC)
    run.provider = gateway.adapter.provider
    run.model_map_json = {
        "standard_analysis": gateway.adapter.model_id,
        "prompt_version": PROMPT_VERSION,
    }
    db.commit()  # 状态外部可见（排队 → 分析）

    # ---- 输入：已确认事实 + 岗位 + 规则分（受保护属性绝不进 prompt） ----
    facts = (
        db.execute(
            select(ProfileFact).where(
                ProfileFact.user_id == plan.user_id, ProfileFact.status == "active"
            )
        )
        .scalars()
        .all()
    )
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
        return _fail(db, run, "JOB_POSTING_NOT_FOUND")
    components = (
        db.execute(
            select(MatchComponent).where(MatchComponent.recommendation_id == rec.id)
        )
        .scalars()
        .all()
    )
    input_doc = build_analysis_input(
        facts=facts, plan=plan, posting=posting, recommendation=rec, components=components
    )
    run.input_fingerprint = input_fingerprint(input_doc)

    # ---- 授权门控：真实供应商必须有独立授权；合成实现不出本机 ----
    consent: ConsentDecision | None = None
    if gateway.adapter.provider in PROVIDERS_REQUIRING_CONSENT:
        consent = check_consent_sync(
            db, plan.user_id, gateway.adapter.provider, ANALYSIS_CONSENT_SCOPE
        )
        if not consent.allowed:
            # 未授权即失败（基础匹配结果仍可用），绝不静默改走其他供应商
            return _fail(db, run, "CONSENT_REQUIRED")

    # ---- 走 Gateway（超时/有限重试/Schema 校验 + 一次修复在 Gateway 内） ----
    try:
        report, usage = gateway.complete_json(
            build_analysis_request(input_doc),
            StandardAnalysisReport,
            consent=consent,
        )
    except LLMSchemaError as exc:
        # Schema 失败前的真实调用已实际扣费：照常记账（_fail 内 commit）
        if exc.usage is not None:
            _record_llm_usage(db, plan.user_id, exc.usage)
            run.cost_tokens_in = exc.usage.tokens_in
            run.cost_tokens_out = exc.usage.tokens_out
            run.cost_amount = exc.usage.amount_estimated
        return _fail(db, run, "SCHEMA_INVALID")
    except (LLMNotConfiguredError, LLMAuthorizationError):
        return _fail(db, run, "CONSENT_REQUIRED")
    except LLMError:
        return _fail(db, run, "MODEL_UNAVAILABLE")

    _record_llm_usage(db, plan.user_id, usage)
    run.cost_tokens_in = usage.tokens_in
    run.cost_tokens_out = usage.tokens_out
    run.cost_amount = usage.amount_estimated
    run.status = "validating"
    run.current_stage = "validating"
    db.commit()

    # ---- 确定性证据校验：虚构事实引用/编造原文 → 明确失败 ----
    violations = validate_report_evidence(report, input_doc)
    if violations:
        logger.warning(
            "analysis_evidence_violations",
            run_id=str(run.id),
            violation_count=len(violations),
            kinds=sorted({v.split(":", 1)[1] for v in violations})[:5],
        )
        return _fail(db, run, "EVIDENCE_VALIDATION_FAILED")

    run.status = "completed"
    run.current_stage = None
    run.completed_at = datetime.now(UTC)
    run.final_report_json = report.model_dump(mode="json")
    db.commit()
    logger.info(
        "analysis_run_completed",
        run_id=str(run.id),
        recommendation_id=str(run.recommendation_id),
        provider=usage.provider,
        model_id=usage.model_id,
        tokens_in=usage.tokens_in,
        tokens_out=usage.tokens_out,
        strengths=len(report.strengths),
        gaps=len(report.gaps),
        risks=len(report.risks),
    )
    return {"status": "completed", "run_id": str(run.id)}


# 经真实端到端验证的供应商（00-master：验证通过前不虚标；合成实现永不入列）
# deepseek：2026-07-30 三链路实跑验证通过（docs/16-real-model-verification.md）。
# 语义是"该 run 的产出确实来自真实供应商"（按 run.provider 逐条判断），
# 合成 run 永远 verified=False；当前模型可用性另看 capabilities 三态。
_VERIFIED_PROVIDERS: frozenset[str] = frozenset({"deepseek"})


def provider_verified(provider: str) -> bool:
    """合成/未经真实验证的供应商结果必须如实标注 not_verified。"""
    return provider in _VERIFIED_PROVIDERS
