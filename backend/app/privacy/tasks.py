"""数据主体权利 Celery 任务（阶段 8）。

- privacy.generate_data_export：异步生成导出 ZIP（幂等；失败落 failed 不伪装）。
- privacy.purge_due_accounts：beat 每小时扫描宽限期到期账号并物理清理；
  子项失败整体 failed，下轮自动重试（execute_account_purge 幂等可重入）。
- privacy.cleanup_expired_export_files：beat 清理过期导出文件（行保留）。
"""

import uuid

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.privacy.service import (
    cleanup_expired_export_files,
    execute_data_export,
    purge_due_accounts,
)
from app.tasks.celery_app import celery_app


def _session() -> tuple[Session, Engine]:
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    return Session(engine), engine


@celery_app.task(name="privacy.generate_data_export", bind=True, max_retries=0)
def generate_data_export_task(self, export_id: str) -> dict:
    db, engine = _session()
    try:
        return execute_data_export(db, uuid.UUID(export_id))
    finally:
        db.close()
        engine.dispose()


@celery_app.task(name="privacy.purge_due_accounts", bind=True, max_retries=0)
def purge_due_accounts_task(self) -> dict:
    db, engine = _session()
    try:
        return purge_due_accounts(db)
    finally:
        db.close()
        engine.dispose()


@celery_app.task(name="privacy.cleanup_expired_export_files", bind=True, max_retries=0)
def cleanup_expired_export_files_task(self) -> dict:
    db, engine = _session()
    try:
        return cleanup_expired_export_files(db)
    finally:
        db.close()
        engine.dispose()
