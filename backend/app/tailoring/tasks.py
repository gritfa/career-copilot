"""定制简历 Celery 任务（阶段 7）。

- 幂等：service 层对非 generating/queued 状态直接跳过，重复投递不重复执行。
- max_retries=0：失败语义由行状态/error_code 承载（用户可见"失败"），
  不做队列层重放；用户可重新触发（生成计入每日额度，导出不计）。
- 日志无正文（service 层保证）。
"""

import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.tailoring.service import execute_resume_export, execute_resume_tailoring
from app.tasks.celery_app import celery_app


@celery_app.task(name="tailoring.generate_resume_draft", bind=True, max_retries=0)
def generate_resume_draft_task(self, version_id: str) -> dict:
    """执行一次岗位定制简历生成。"""
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    db = Session(engine)
    try:
        return execute_resume_tailoring(db, uuid.UUID(version_id))
    finally:
        db.close()
        engine.dispose()


@celery_app.task(name="tailoring.export_resume", bind=True, max_retries=0)
def export_resume_task(self, export_id: str) -> dict:
    """执行一次 DOCX/PDF 导出。"""
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    db = Session(engine)
    try:
        return execute_resume_export(db, uuid.UUID(export_id))
    finally:
        db.close()
        engine.dispose()
