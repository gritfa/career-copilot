"""来源采集 Celery 任务：每日调度、限速、熔断、增量与统计。

不变量（docs/06 第 5 节）：
- 任一条目失败时 run 状态不得为 success（DB CHECK 双保险）。
- 验证码 / 禁止访问错误立即熔断（source.status = circuit_open）。
- 连续失败达到阈值熔断；成功后清零。
- run_key（默认当天日期）幂等：同源同日成功过不重复采集。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import redis as redis_sync
import structlog
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.db.models import AuditEvent, JobSource, SourceRun
from app.jobs.adapters.base import (
    AccessForbiddenError,
    CaptchaRequiredError,
    SourceAccessError,
)
from app.jobs.pipeline import ingest_raw
from app.jobs.ratelimit import wait_for_token
from app.jobs.registry import (
    DEFAULT_CIRCUIT_FAILURE_THRESHOLD,
    SOURCE_SEEDS,
    get_adapter,
    seed_sources_sync,
)
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("app.jobs.tasks")

# 立即熔断的错误码（出现即停止自动化路径，规格硬边界）
_IMMEDIATE_BREAK_CODES = {"CAPTCHA_REQUIRED", "ACCESS_FORBIDDEN"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _task_session() -> tuple[Session, Engine]:
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    return Session(engine), engine


def _open_circuit(db: Session, source: JobSource, reason_code: str) -> None:
    source.status = "circuit_open"
    db.add(
        AuditEvent(
            actor_type="system",
            action="job_source_circuit_opened",
            resource_type="job_source",
            resource_id=str(source.id),
            result="failure",
            reason_code=reason_code,
        )
    )
    logger.warning(
        "job_source_circuit_opened", source_key=source.source_key, reason_code=reason_code
    )


def _finalize_run(
    db: Session,
    source: JobSource,
    run: SourceRun,
    *,
    items_seen: int,
    items_new: int,
    items_failed: int,
    item_error_code: str | None,
    fatal_code: str | None,
) -> str:
    """收尾：写 run 状态 + 熔断计数。任一失败绝不标 success。"""
    run.items_seen = items_seen
    run.items_new = items_new
    run.items_failed = items_failed
    run.error_code = item_error_code
    run.completed_at = _utcnow()
    threshold = int(
        (source.rate_limit_config or {}).get(
            "circuit_failure_threshold", DEFAULT_CIRCUIT_FAILURE_THRESHOLD
        )
    )

    if fatal_code is not None:
        run.status = "failed"
        run.error_code = fatal_code
        source.consecutive_failures += 1
        if fatal_code in _IMMEDIATE_BREAK_CODES:
            _open_circuit(db, source, fatal_code)
        elif source.consecutive_failures >= threshold:
            _open_circuit(db, source, "CONSECUTIVE_FAILURES")
    elif run.items_failed == 0:
        run.status = "success"
        source.consecutive_failures = 0
    elif run.items_failed < run.items_seen:
        run.status = "partial_failure"
        run.error_code = run.error_code or "ITEMS_FAILED"
    else:
        run.status = "failed"
        run.error_code = run.error_code or "ALL_ITEMS_FAILED"
        source.consecutive_failures += 1
        if source.consecutive_failures >= threshold:
            _open_circuit(db, source, "CONSECUTIVE_FAILURES")

    if run.status != "success":
        # 有限指数退避（封顶 24h）
        backoff_minutes = min(24 * 60, 15 * (2 ** min(source.consecutive_failures, 7)))
        run.next_retry_at = _utcnow() + timedelta(minutes=backoff_minutes)

    db.commit()
    logger.info(
        "source_run_finished",
        source_key=source.source_key,
        run_id=str(run.id),
        status=run.status,
        items_seen=run.items_seen,
        items_new=run.items_new,
        items_failed=run.items_failed,
        error_code=run.error_code,
    )
    return run.status


@celery_app.task(name="jobs.sync_source", bind=True, max_retries=0)
def sync_source_task(self, source_key: str, run_key: str | None = None) -> str:
    """采集单个来源：discover → 限速 fetch_detail → 管道入库 → run 统计。"""
    db, engine = _task_session()
    try:
        seed_sources_sync(db)
        source = db.execute(
            select(JobSource).where(JobSource.source_key == source_key)
        ).scalar_one_or_none()
        if source is None:
            return "source_not_found"
        if source.source_type == "user_import":
            return "not_applicable"
        adapter = get_adapter(source_key)
        if adapter is None:
            # link_out/import_only（BOSS）：没有允许的自动访问方式，绝不抓取
            return "no_adapter_import_only"
        if source.status != "enabled":
            logger.info("source_skipped", source_key=source_key, status=source.status)
            return f"skipped:{source.status}"

        run_key = run_key or _utcnow().strftime("%Y%m%d")
        run = db.execute(
            select(SourceRun).where(
                SourceRun.job_source_id == source.id, SourceRun.run_key == run_key
            )
        ).scalar_one_or_none()
        if run is not None and run.status == "success":
            return "already_ran"
        if run is None:
            run = SourceRun(id=uuid.uuid4(), job_source_id=source.id, run_key=run_key)
            db.add(run)
        run.status = "running"
        run.started_at = _utcnow()
        run.completed_at = None
        run.items_seen = 0
        run.items_new = 0
        run.items_failed = 0
        run.error_code = None
        db.commit()

        settings = get_settings()
        redis_client = redis_sync.Redis.from_url(settings.redis_url)
        rate_cfg = source.rate_limit_config or {}
        per_minute = int(rate_cfg.get("per_minute", 6))
        burst = rate_cfg.get("burst")

        # 计数放本地变量：条目失败 rollback 不能吞掉统计
        items_seen = items_new = items_failed = 0
        item_error_code: str | None = None
        fatal_code: str | None = None
        try:
            cursor: str | None = None
            while True:
                page = asyncio.run(adapter.discover(cursor))
                for ref in page.refs:
                    items_seen += 1
                    if not wait_for_token(
                        redis_client, source_key, per_minute=per_minute, burst=burst
                    ):
                        items_failed += 1
                        item_error_code = "RATE_LIMIT_TIMEOUT"
                        continue
                    try:
                        raw = asyncio.run(adapter.fetch_detail(ref))
                        result = ingest_raw(db, source, raw)
                        db.commit()
                        if result.created:
                            items_new += 1
                    except (CaptchaRequiredError, AccessForbiddenError) as exc:
                        db.rollback()
                        items_failed += 1
                        fatal_code = exc.error_code
                        break
                    except SourceAccessError as exc:
                        db.rollback()
                        items_failed += 1
                        item_error_code = exc.error_code
                    except Exception:
                        db.rollback()
                        items_failed += 1
                        item_error_code = "ITEM_INGEST_FAILED"
                        logger.warning(
                            "job_item_ingest_failed",
                            source_key=source_key,
                            source_job_id=ref.source_job_id,
                        )
                if fatal_code is not None or page.next_cursor is None:
                    break
                cursor = page.next_cursor
        except (CaptchaRequiredError, AccessForbiddenError) as exc:
            db.rollback()
            fatal_code = exc.error_code
        except SourceAccessError as exc:
            db.rollback()
            fatal_code = exc.error_code
        except Exception:
            db.rollback()
            fatal_code = "SOURCE_RUN_CRASHED"
            logger.exception("source_run_crashed", source_key=source_key)
        finally:
            redis_client.close()

        return _finalize_run(
            db,
            source,
            run,
            items_seen=items_seen,
            items_new=items_new,
            items_failed=items_failed,
            item_error_code=item_error_code,
            fatal_code=fatal_code,
        )
    finally:
        db.close()
        engine.dispose()


@celery_app.task(name="jobs.sync_all_sources")
def sync_all_sources_task() -> dict[str, str]:
    """每日一次全量来源调度（Celery beat 注册）；来源间随机抖动错峰。"""
    import random

    results: dict[str, str] = {}
    eager = get_settings().celery_task_always_eager
    for seed in SOURCE_SEEDS:
        source_key = seed["source_key"]
        if get_adapter(source_key) is None:
            continue  # boss/user_import：无自动采集路径
        if eager:
            results[source_key] = sync_source_task.apply(args=(source_key,)).get()
        else:
            sync_source_task.apply_async(
                args=(source_key,), countdown=random.randint(0, 1800)
            )
            results[source_key] = "scheduled"
    return results
