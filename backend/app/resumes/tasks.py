"""简历 Celery 任务：幂等解析 + 删除清理骨架。

安全：
- 日志只输出 ID / 状态 / 计数，严禁简历正文、联系方式、文件名。
- 幂等：parse 行唯一（idempotency_key），已成功的解析重复投递直接返回，
  候选不重复入库。
- 解析超时保护：extract+抽取放入受限线程，超时记 PARSE_TIMEOUT 失败；
  worker 级另有 task_time_limit 硬兜底。
- 扫描件提不出文本 → NO_TEXT_LAYER 失败状态，不伪装成功。
"""

import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import UTC, datetime

import structlog
from sqlalchemy import Engine, create_engine, delete, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import (
    AuditEvent,
    FactCandidate,
    FactEvidence,
    Resume,
    ResumeParse,
)
from app.integrations.llm_structured import ExtractionOutput
from app.integrations.storage import get_storage
from app.resumes.extract import ExtractionError, NoTextLayerError, extract_text
from app.resumes.parser import RuleBasedExtractor, filter_protected, quote_hash
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("app.resumes.tasks")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _task_session() -> tuple[Session, Engine]:
    """任务级同步会话（每次任务独立引擎，避免跨环境连接串用）。"""
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    return Session(engine), engine


def _extract_and_parse(data: bytes, media_type: str) -> tuple[str, ExtractionOutput]:
    """文本提取 + 规则抽取（在受时限线程中运行）。"""
    text = extract_text(data, media_type)
    output = filter_protected(RuleBasedExtractor().extract(text))
    return text, output


def _fail_parse(
    db: Session,
    parse: ResumeParse,
    resume: Resume,
    error_code: str,
    text_extract_status: str | None = None,
) -> None:
    parse.status = "failed"
    parse.error_code = error_code
    parse.completed_at = _utcnow()
    resume.status = "parse_failed"
    if text_extract_status:
        resume.text_extract_status = text_extract_status
    db.add(
        AuditEvent(
            actor_type="system",
            action="resume_parse_failed",
            resource_type="resume_parse",
            resource_id=str(parse.id),
            result="failure",
            reason_code=error_code,
        )
    )
    db.commit()
    logger.info("resume_parse_failed", parse_id=str(parse.id), error_code=error_code)


@celery_app.task(bind=True, name="resumes.parse_resume", max_retries=2)
def parse_resume_task(self, parse_id: str) -> str:
    """解析一份简历：提取文本 → 规则抽取候选事实（幂等）。"""
    settings = get_settings()
    db, engine = _task_session()
    try:
        parse = db.get(ResumeParse, uuid.UUID(parse_id))
        if parse is None:
            return "parse_not_found"
        if parse.status == "succeeded":
            return "already_succeeded"  # 幂等：不重复入库
        resume = db.get(Resume, parse.resume_id)
        if resume is None or resume.status in ("deleting", "deleted"):
            parse.status = "failed"
            parse.error_code = "RESUME_GONE"
            parse.completed_at = _utcnow()
            db.commit()
            return "resume_gone"

        parse.status = "running"
        parse.started_at = parse.started_at or _utcnow()
        resume.status = "parsing"
        db.commit()

        try:
            data = get_storage().open(resume.storage_key)
        except FileNotFoundError:
            _fail_parse(db, parse, resume, "STORAGE_OBJECT_MISSING")
            return "failed"

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(_extract_and_parse, data, resume.media_type)
        try:
            text, output = future.result(timeout=settings.parse_timeout_seconds)
        except FutureTimeoutError:
            future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            _fail_parse(db, parse, resume, "PARSE_TIMEOUT", text_extract_status="failed")
            return "failed"
        except NoTextLayerError:
            executor.shutdown(wait=False)
            _fail_parse(db, parse, resume, "NO_TEXT_LAYER", text_extract_status="no_text_layer")
            return "failed"
        except ExtractionError:
            executor.shutdown(wait=False)
            _fail_parse(db, parse, resume, "EXTRACTION_FAILED", text_extract_status="failed")
            return "failed"
        else:
            executor.shutdown(wait=False)

        # 提取文本分层存储（key 只含 UUID）
        text_key = f"extracted/{resume.user_id}/{parse.id}.txt"
        get_storage().save(text_key, text.encode("utf-8"))
        resume.text_extract_status = "succeeded"
        parse.extracted_text_storage_key = text_key
        parse.protected_discarded_count = output.protected_discarded_count

        # 幂等：该 parse 已有候选（历史重复投递）就不再插入
        existing = (
            db.execute(
                select(FactCandidate.id).where(FactCandidate.resume_parse_id == parse.id).limit(1)
            )
        ).first()
        inserted = 0
        if existing is None:
            for draft in output.candidates:
                db.add(
                    FactCandidate(
                        resume_parse_id=parse.id,
                        fact_type=draft.fact_type,
                        value_json=draft.value_json,
                        source_span_start=draft.span_start,
                        source_span_end=draft.span_end,
                        source_quote_hash=quote_hash(draft.quote),
                        confidence=draft.confidence,
                        status="pending",
                    )
                )
                inserted += 1

        parse.status = "succeeded"
        parse.completed_at = _utcnow()
        resume.status = "parsed"
        db.add(
            AuditEvent(
                actor_type="system",
                action="resume_parsed",
                resource_type="resume_parse",
                resource_id=str(parse.id),
                result="success",
                reason_code=(
                    f"candidates:{inserted};protected_discarded:"
                    f"{output.protected_discarded_count}"
                ),
            )
        )
        db.commit()
        logger.info(
            "resume_parsed",
            parse_id=str(parse.id),
            candidate_count=inserted,
            protected_discarded=output.protected_discarded_count,
        )
        return "succeeded"
    finally:
        db.close()
        engine.dispose()


@celery_app.task(bind=True, name="resumes.cleanup_resume", max_retries=3)
def cleanup_resume_task(self, resume_id: str) -> str:
    """删除单份简历的异步清理：存储对象 + 解析/候选行 + 证据脱链。

    已确认的 profile_facts 保留（用户确认过的派生层），
    其证据 resume_id 置空、保留 evidence_hash 与最小摘录。
    """
    db, engine = _task_session()
    try:
        resume = db.get(Resume, uuid.UUID(resume_id))
        if resume is None:
            return "resume_not_found"
        if resume.status == "deleted":
            return "already_deleted"  # 幂等

        storage = get_storage()
        parses = (
            db.execute(select(ResumeParse).where(ResumeParse.resume_id == resume.id))
            .scalars()
            .all()
        )
        for parse in parses:
            if parse.extracted_text_storage_key:
                storage.delete(parse.extracted_text_storage_key)
        storage.delete(resume.storage_key)

        # 证据脱链（保留 hash/摘录），解析行级联删除候选
        db.execute(
            update(FactEvidence)
            .where(FactEvidence.resume_id == resume.id)
            .values(resume_id=None)
        )
        db.execute(delete(ResumeParse).where(ResumeParse.resume_id == resume.id))

        resume.status = "deleted"
        resume.deleted_at = resume.deleted_at or _utcnow()
        db.add(
            AuditEvent(
                actor_type="system",
                action="resume_deleted",
                resource_type="resume",
                resource_id=str(resume.id),
                result="success",
            )
        )
        db.commit()
        logger.info("resume_cleanup_done", resume_id=str(resume.id))
        return "deleted"
    finally:
        db.close()
        engine.dispose()


@celery_app.task(name="resumes.cleanup_expired_uploads")
def cleanup_expired_uploads_task() -> str:
    """清理骨架：未确认的上传会话过期后（Redis TTL 已到）孤儿存储对象回收。

    MVP：上传会话在 Redis 带 TTL，元数据自动过期；本任务留作
    定时扫描 staging 前缀孤儿对象的挂载点（尚未接入 beat 调度）。
    """
    return "noop"
