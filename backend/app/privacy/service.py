"""数据主体权利执行层（阶段 8，docs/08 第 9 节）：同步实现，供 Celery 任务与 CLI 复用。

- 数据导出：用户本人全部数据 → ZIP（export.json + 自有简历原文件），
  只含本人数据；绝不包含其他用户、系统密钥、内部提示词或风控规则。
- 账号硬删：软删（deletion_pending）→ 宽限期 → 本模块物理清理
  （文件、向量、DB 行、auth_tokens），产出可验证清单；
  任一子项失败整体标 failed 且不删 DB 行，可安全重试。
- 日志红线：本模块日志只含 ID/计数/错误码，无邮箱、无正文、无文件名。
"""

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_sync
from app.core.config import get_settings
from app.db.models import (
    AccountPurgeRun,
    AgentRun,
    AuthToken,
    CanonicalJob,
    Company,
    CompanyPreference,
    Consent,
    DataExport,
    FactCandidate,
    FactEvidence,
    JobPosting,
    JobSnapshot,
    LearnedPreference,
    MatchComponent,
    ProfileFact,
    ProfileVector,
    Recommendation,
    Resume,
    ResumeExport,
    ResumeParse,
    ResumeVersion,
    SearchPlan,
    UsageLedger,
    User,
    UserFeedback,
)
from app.db.models import Session as DbSession
from app.integrations.storage import get_storage

logger = structlog.get_logger("app.privacy")

EXPORT_SCHEMA_VERSION = "user_data_export_v1"

_MEDIA_EXT = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}


def _jsonable(value: Any) -> Any:
    """DB 值 → JSON 可序列化（UUID/时间/Decimal → 字符串/数字）。"""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):  # date
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _rows(db: Session, stmt) -> list:
    return list(db.execute(stmt).scalars().all())


# ---------------- 数据导出 ----------------


def collect_export_payload(db: Session, user: User) -> dict[str, Any]:
    """收集该用户全部个人数据（JSON 部分）。

    只查本人归属行；岗位侧只带该用户推荐引用到的标题/公司名等最小上下文。
    不包含：其他用户数据、密钥、内部提示词、风控/评分内部规则、向量数值。
    """
    plans = _rows(db, select(SearchPlan).where(SearchPlan.user_id == user.id))
    plan_ids = [p.id for p in plans]
    resumes = _rows(db, select(Resume).where(Resume.user_id == user.id))
    resume_ids = [r.id for r in resumes]

    parses = (
        _rows(db, select(ResumeParse).where(ResumeParse.resume_id.in_(resume_ids)))
        if resume_ids
        else []
    )
    parse_ids = [p.id for p in parses]
    candidates = (
        _rows(db, select(FactCandidate).where(FactCandidate.resume_parse_id.in_(parse_ids)))
        if parse_ids
        else []
    )

    facts = _rows(db, select(ProfileFact).where(ProfileFact.user_id == user.id))
    fact_ids = [f.id for f in facts]
    evidence = (
        _rows(db, select(FactEvidence).where(FactEvidence.profile_fact_id.in_(fact_ids)))
        if fact_ids
        else []
    )

    recs = (
        _rows(db, select(Recommendation).where(Recommendation.search_plan_id.in_(plan_ids)))
        if plan_ids
        else []
    )
    rec_ids = [r.id for r in recs]
    components = (
        _rows(db, select(MatchComponent).where(MatchComponent.recommendation_id.in_(rec_ids)))
        if rec_ids
        else []
    )
    feedback = (
        _rows(db, select(UserFeedback).where(UserFeedback.recommendation_id.in_(rec_ids)))
        if rec_ids
        else []
    )
    # 岗位标题/公司名：仅该用户推荐引用到的最小上下文（公开岗位信息，非他人个人数据）
    job_ids = {r.canonical_job_id for r in recs}
    job_titles: dict[str, str] = {}
    if job_ids:
        for job, company_name in db.execute(
            select(CanonicalJob, Company.canonical_name)
            .outerjoin(Company, Company.id == CanonicalJob.company_id)
            .where(CanonicalJob.id.in_(job_ids))
        ).all():
            title = job.title_normalized
            job_titles[str(job.id)] = f"{company_name} · {title}" if company_name else title

    prefs = (
        _rows(
            db,
            select(CompanyPreference).where(CompanyPreference.search_plan_id.in_(plan_ids)),
        )
        if plan_ids
        else []
    )
    runs = _rows(db, select(AgentRun).where(AgentRun.user_id == user.id))
    versions = _rows(db, select(ResumeVersion).where(ResumeVersion.user_id == user.id))
    exports = _rows(db, select(ResumeExport).where(ResumeExport.user_id == user.id))
    learned = _rows(
        db, select(LearnedPreference).where(LearnedPreference.user_id == user.id)
    )
    consents = _rows(db, select(Consent).where(Consent.user_id == user.id))
    ledger = _rows(db, select(UsageLedger).where(UsageLedger.user_id == user.id))

    return _jsonable(
        {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "generated_at": datetime.now(UTC),
            "account": {
                "id": user.id,
                "email": user.email_normalized,
                "role": user.role,
                "status": user.status,
                "created_at": user.created_at,
                "terms_version": user.terms_version,
                "privacy_version": user.privacy_version,
                "age_attested_at": user.age_attested_at,
            },
            "consents": [
                {
                    "provider": c.provider,
                    "scope": c.scope,
                    "notice_version": c.notice_version,
                    "granted_at": c.granted_at,
                    "revoked_at": c.revoked_at,
                }
                for c in consents
            ],
            "resumes": [
                {
                    "id": r.id,
                    "original_filename": r.original_filename,
                    "media_type": r.media_type,
                    "size_bytes": r.size_bytes,
                    "sha256": r.sha256,
                    "status": r.status,
                    "uploaded_at": r.uploaded_at,
                    "file_in_zip": (
                        f"files/resumes/{r.id}.{_MEDIA_EXT.get(r.media_type, 'bin')}"
                        if r.status not in ("deleting", "deleted")
                        else None
                    ),
                }
                for r in resumes
            ],
            "resume_parses": [
                {
                    "id": p.id,
                    "resume_id": p.resume_id,
                    "parser_name": p.parser_name,
                    "parser_version": p.parser_version,
                    "status": p.status,
                    "completed_at": p.completed_at,
                }
                for p in parses
            ],
            "fact_candidates": [
                {
                    "id": c.id,
                    "fact_type": c.fact_type,
                    "value": c.value_json,
                    "status": c.status,
                    "confidence": c.confidence,
                }
                for c in candidates
            ],
            "profile_facts": [
                {
                    "id": f.id,
                    "fact_type": f.fact_type,
                    "value": f.value_json,
                    "status": f.status,
                    "provenance_type": f.provenance_type,
                    "confirmed_by_user_at": f.confirmed_by_user_at,
                }
                for f in facts
            ],
            "fact_evidence": [
                {
                    "profile_fact_id": e.profile_fact_id,
                    "resume_id": e.resume_id,
                    "display_excerpt": e.display_excerpt,
                }
                for e in evidence
            ],
            "search_plans": [
                {
                    "id": p.id,
                    "name": p.name,
                    "role_family": p.role_family,
                    "status": p.status,
                    "city_codes": p.city_codes,
                    "work_modes": p.work_modes,
                    "minimum_monthly_salary": p.minimum_monthly_salary,
                    "target_monthly_salary": p.target_monthly_salary,
                    "minimum_match_score": p.minimum_match_score,
                    "allow_outsourcing": p.allow_outsourcing,
                    "created_at": p.created_at,
                }
                for p in plans
            ],
            "company_preferences": [
                {
                    "search_plan_id": cp.search_plan_id,
                    "company_id": cp.company_id,
                    "preference": cp.preference,
                }
                for cp in prefs
            ],
            "recommendations": [
                {
                    "id": r.id,
                    "search_plan_id": r.search_plan_id,
                    "job": job_titles.get(str(r.canonical_job_id)),
                    "score_total": r.score_total,
                    "grade": r.grade,
                    "hard_filter_status": r.hard_filter_status,
                    "recommended_on": r.recommended_on,
                    "components": [
                        {
                            "component": c.component,
                            "score": c.score,
                            "weight": c.weight,
                            "gap_level": c.gap_level,
                        }
                        for c in components
                        if c.recommendation_id == r.id
                    ],
                }
                for r in recs
            ],
            "feedback": [
                {
                    "recommendation_id": f.recommendation_id,
                    "sentiment": f.sentiment,
                    "reason_code": f.reason_code,
                    "note": f.optional_note,
                    "created_at": f.created_at,
                }
                for f in feedback
            ],
            "analyses": [
                {
                    "id": a.id,
                    "recommendation_id": a.recommendation_id,
                    "status": a.status,
                    "provider": a.provider,
                    "completed_at": a.completed_at,
                    "report": a.final_report_json,
                }
                for a in runs
            ],
            "resume_versions": [
                {
                    "id": v.id,
                    "kind": v.kind,
                    "recommendation_id": v.recommendation_id,
                    "status": v.status,
                    "template_id": v.template_id,
                    "content": v.content_json,
                    "changes": v.changes_json,
                    "confirmed_at": v.confirmed_at,
                    "created_at": v.created_at,
                }
                for v in versions
            ],
            "resume_exports": [
                {
                    "id": e.id,
                    "resume_version_id": e.resume_version_id,
                    "format": e.format,
                    "status": e.status,
                    "created_at": e.created_at,
                }
                for e in exports
            ],
            "learned_preferences": [
                {"version": lp.version, "weights": lp.weights_json, "status": lp.status}
                for lp in learned
            ],
            "usage_ledger": [
                {
                    "provider": u.provider,
                    "model": u.model,
                    "operation_type": u.operation_type,
                    "tokens_in": u.tokens_in,
                    "tokens_out": u.tokens_out,
                    "amount_estimated": float(u.amount_estimated),
                    "occurred_at": u.occurred_at,
                }
                for u in ledger
            ],
        }
    )


def execute_data_export(db: Session, export_id: uuid.UUID) -> dict[str, Any]:
    """生成导出 ZIP（幂等：非 queued 状态直接跳过）。"""
    export = db.get(DataExport, export_id)
    if export is None:
        return {"status": "not_found"}
    if export.status != "queued":
        return {"status": export.status, "skipped": True}
    user = db.get(User, export.user_id)
    if user is None:
        export.status = "failed"
        export.error_code = "USER_NOT_FOUND"
        db.commit()
        return {"status": "failed"}

    export.status = "running"
    db.commit()

    try:
        payload = collect_export_payload(db, user)
        storage = get_storage()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "export.json", json.dumps(payload, ensure_ascii=False, indent=2)
            )
            # 自有简历原文件（仅未删除且文件仍存在的）
            for r in _rows(db, select(Resume).where(Resume.user_id == user.id)):
                if r.status in ("deleting", "deleted"):
                    continue
                if storage.exists(r.storage_key):
                    ext = _MEDIA_EXT.get(r.media_type, "bin")
                    zf.writestr(f"files/resumes/{r.id}.{ext}", storage.open(r.storage_key))
        data = buf.getvalue()

        key = f"data_exports/{export.id}.zip"
        storage.save(key, data)
        now = datetime.now(UTC)
        export.storage_key = key
        export.file_sha256 = hashlib.sha256(data).hexdigest()
        export.size_bytes = len(data)
        export.expires_at = now + timedelta(seconds=get_settings().data_export_ttl_seconds)
        export.completed_at = now
        export.status = "succeeded"
        record_audit_sync(
            db,
            actor_type="system",
            action="data_export_generated",
            resource_type="data_export",
            resource_id=str(export.id),
        )
        db.commit()
        logger.info("data_export_succeeded", export_id=str(export.id), size=len(data))
        return {"status": "succeeded", "size_bytes": len(data)}
    except Exception:
        db.rollback()
        export = db.get(DataExport, export_id)
        if export is not None:
            export.status = "failed"
            export.error_code = "EXPORT_GENERATION_FAILED"
            export.completed_at = datetime.now(UTC)
            db.commit()
        logger.exception("data_export_failed", export_id=str(export_id))
        return {"status": "failed"}


# ---------------- 账号硬删（软删 → 宽限期 → 物理清理） ----------------


def _count(db: Session, stmt) -> int:
    return int(db.execute(stmt).scalar_one())


def execute_account_purge(db: Session, user_id: uuid.UUID) -> dict[str, Any]:
    """物理清理一个 deletion_pending 账号：文件 → auth_tokens → 用户行（级联）。

    不变量：
    - 只处理 deletion_pending 的账号（active 账号绝不误删）。
    - 任一文件删除失败 → 整体 failed，DB 行保留，可安全重试。
    - 成功后写可验证清单（各类计数）+ 审计事件；user 行连同级联全部删除，
      profile_vectors（pgvector）随 search_plans 级联真删。
    """
    user = db.get(User, user_id)
    if user is None:
        return {"status": "already_purged"}
    if user.status != "deletion_pending":
        return {"status": "not_pending"}

    # get-or-create 未完成的 purge run（可重试）
    run = db.execute(
        select(AccountPurgeRun)
        .where(AccountPurgeRun.user_id == user_id, AccountPurgeRun.status != "succeeded")
        .order_by(AccountPurgeRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if run is None:
        run = AccountPurgeRun(user_id=user_id, status="running", attempts=0)
        db.add(run)
    run.status = "running"
    run.attempts += 1
    db.commit()

    # 1) 收集要删的存储对象（简历原件、解析文本、导出文件、数据导出 ZIP、
    #    个人导入岗位的原始快照——用户粘贴的岗位正文属于个人提交内容）
    file_keys: list[str] = []
    resumes = _rows(db, select(Resume).where(Resume.user_id == user_id))
    resume_ids = [r.id for r in resumes]
    file_keys += [r.storage_key for r in resumes]
    parses = (
        _rows(db, select(ResumeParse).where(ResumeParse.resume_id.in_(resume_ids)))
        if resume_ids
        else []
    )
    file_keys += [p.extracted_text_storage_key for p in parses if p.extracted_text_storage_key]
    exports = _rows(db, select(ResumeExport).where(ResumeExport.user_id == user_id))
    file_keys += [e.storage_key for e in exports if e.storage_key and not e.file_purged_at]
    data_exports = _rows(db, select(DataExport).where(DataExport.user_id == user_id))
    file_keys += [e.storage_key for e in data_exports if e.storage_key and not e.file_purged_at]
    imported_postings = _rows(
        db, select(JobPosting).where(JobPosting.imported_by_user_id == user_id)
    )
    import_snapshot_ids = [p.snapshot_id for p in imported_postings if p.snapshot_id]
    import_snapshots = (
        _rows(db, select(JobSnapshot).where(JobSnapshot.id.in_(import_snapshot_ids)))
        if import_snapshot_ids
        else []
    )
    file_keys += [s.storage_key for s in import_snapshots]

    # 2) 逐个删文件；失败只记 key 的哈希（不落 key/文件名到清单与日志）
    failed_files = 0
    deleted_files = 0
    storage = get_storage()
    for key in file_keys:
        try:
            if storage.exists(key):
                storage.delete(key)
            deleted_files += 1
        except Exception:
            failed_files += 1
            logger.warning(
                "purge_file_delete_failed",
                user_id=str(user_id),
                key_hash=hashlib.sha256(key.encode()).hexdigest()[:16],
            )

    if failed_files:
        run.status = "failed"
        run.error_code = "FILE_DELETE_FAILED"
        run.manifest_json = {
            "files_deleted": deleted_files,
            "files_failed": failed_files,
        }
        record_audit_sync(
            db,
            actor_type="system",
            action="account_purge",
            resource_type="user",
            resource_id=str(user_id),
            result="failure",
            reason_code="FILE_DELETE_FAILED",
        )
        db.commit()
        return {"status": "failed", "files_failed": failed_files}

    # 3) 删除前统计计数（可验证清单）
    plan_ids = [
        p.id for p in _rows(db, select(SearchPlan).where(SearchPlan.user_id == user_id))
    ]
    rec_count = (
        _count(
            db,
            select(func.count())
            .select_from(Recommendation)
            .where(Recommendation.search_plan_id.in_(plan_ids)),
        )
        if plan_ids
        else 0
    )
    vector_count = (
        _count(
            db,
            select(func.count())
            .select_from(ProfileVector)
            .where(ProfileVector.search_plan_id.in_(plan_ids)),
        )
        if plan_ids
        else 0
    )
    manifest = {
        "files_deleted": deleted_files,
        "files_failed": 0,
        "resumes": len(resumes),
        "resume_parses": len(parses),
        "profile_facts": _count(
            db,
            select(func.count()).select_from(ProfileFact).where(ProfileFact.user_id == user_id),
        ),
        "search_plans": len(plan_ids),
        "recommendations": rec_count,
        "profile_vectors": vector_count,
        "agent_runs": _count(
            db, select(func.count()).select_from(AgentRun).where(AgentRun.user_id == user_id)
        ),
        "resume_versions": _count(
            db,
            select(func.count())
            .select_from(ResumeVersion)
            .where(ResumeVersion.user_id == user_id),
        ),
        "resume_exports": len(exports),
        "data_exports": len(data_exports),
        "consents": _count(
            db, select(func.count()).select_from(Consent).where(Consent.user_id == user_id)
        ),
        "sessions": _count(
            db, select(func.count()).select_from(DbSession).where(DbSession.user_id == user_id)
        ),
    }

    # 4) auth_tokens 无 user FK（按邮箱存），必须显式删除
    tokens = _rows(
        db, select(AuthToken).where(AuthToken.email_normalized == user.email_normalized)
    )
    for t in tokens:
        db.delete(t)
    manifest["auth_tokens"] = len(tokens)

    # 4.5) 个人导入岗位清理（阶段 10 任务 B）：私有 canonical（连带 job_vectors /
    #      recommendations 级联）、导入 posting、原始快照行全部物理删除——
    #      注销后不再出现在任何用户的候选池、来源链接或有效性检查里。
    owned_private_job_ids = [
        j.id
        for j in _rows(
            db,
            select(CanonicalJob).where(
                CanonicalJob.visibility == "private",
                CanonicalJob.owner_user_id == user_id,
            ),
        )
    ]
    for posting in imported_postings:
        db.delete(posting)
    db.flush()
    if owned_private_job_ids:
        db.execute(sa_delete(CanonicalJob).where(CanonicalJob.id.in_(owned_private_job_ids)))
    if import_snapshot_ids:
        # job_snapshots append-only 触发器的唯一放行例外：本事务显式声明注销硬删
        db.execute(select(func.set_config("app.allow_snapshot_purge", "1", True)))
        db.execute(sa_delete(JobSnapshot).where(JobSnapshot.id.in_(import_snapshot_ids)))
    manifest["imported_postings"] = len(imported_postings)
    manifest["private_canonical_jobs"] = len(owned_private_job_ids)
    manifest["job_snapshots_purged"] = len(import_snapshot_ids)

    # 5) 删用户行 → 级联删除全部归属行；usage_ledger.user_id SET NULL（匿名化）；
    #    个人导入岗位已在 4.5 物理删除，不走 SET NULL 匿名化
    db.delete(user)
    run.status = "succeeded"
    run.manifest_json = manifest
    run.error_code = None
    run.completed_at = datetime.now(UTC)
    record_audit_sync(
        db,
        actor_type="system",
        action="account_purged",
        resource_type="user",
        resource_id=str(user_id),
    )
    db.commit()
    logger.info("account_purged", user_id=str(user_id), manifest=manifest)
    return {"status": "succeeded", "manifest": manifest}


def purge_due_accounts(db: Session) -> dict[str, Any]:
    """扫描宽限期已到的注销账号并逐个物理清理（Celery beat 每小时）。"""
    now = datetime.now(UTC)
    due = _rows(
        db,
        select(User).where(User.status == "deletion_pending", User.purge_after <= now),
    )
    results = {"due": len(due), "succeeded": 0, "failed": 0}
    for user in due:
        outcome = execute_account_purge(db, user.id)
        if outcome["status"] == "succeeded":
            results["succeeded"] += 1
        elif outcome["status"] == "failed":
            results["failed"] += 1
    return results


def cleanup_expired_export_files(db: Session) -> dict[str, Any]:
    """过期导出文件清理（数据导出 ZIP + 简历导出文件）：行保留，文件删除。"""
    now = datetime.now(UTC)
    storage = get_storage()
    cleaned = 0
    for model in (DataExport, ResumeExport):
        rows = _rows(
            db,
            select(model).where(
                model.expires_at.isnot(None),
                model.expires_at <= now,
                model.file_purged_at.is_(None),
                model.storage_key.isnot(None),
            ),
        )
        for row in rows:
            try:
                if storage.exists(row.storage_key):
                    storage.delete(row.storage_key)
                row.file_purged_at = now
                cleaned += 1
            except Exception:
                logger.warning("export_file_cleanup_failed", export_id=str(row.id))
    db.commit()
    return {"cleaned": cleaned}
