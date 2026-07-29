"""标准分析 Celery 任务（阶段 6）。

- 幂等：service 层对非 queued 状态直接跳过，重复投递不重复执行。
- max_retries=0：失败语义由 run.status/error_code 承载（用户可见"失败"），
  不做队列层重放；用户可重新手动触发（计入每日额度）。
- 日志无正文（service 层保证）。
"""

import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.agents.service import execute_standard_analysis
from app.core.config import get_settings
from app.tasks.celery_app import celery_app


@celery_app.task(name="agents.run_standard_analysis", bind=True, max_retries=0)
def run_standard_analysis_task(self, run_id: str) -> dict:
    """执行一次单模型标准分析。"""
    engine = create_engine(get_settings().sync_database_url, poolclass=NullPool)
    db = Session(engine)
    try:
        return execute_standard_analysis(db, uuid.UUID(run_id))
    finally:
        db.close()
        engine.dispose()
