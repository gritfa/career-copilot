"""Celery 应用：broker/backend 用 Redis（docs/02 架构）。

- 任务必须幂等、有限重试、可观察；不可重试错误不得无限重放。
- 测试用 CELERY_TASK_ALWAYS_EAGER=1（dispatch_task 同步 apply），
  任务代码本身仍是真实 Celery 任务，worker 部署方式不变：
  ``celery -A app.tasks.celery_app worker``
"""

from typing import Any

from celery import Celery, Task

from app.core.config import get_settings


def create_celery_app() -> Celery:
    settings = get_settings()
    celery = Celery(
        "careercopilot",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    celery.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        # 硬性超时兜底（worker 级）；任务内部另有 parse_timeout_seconds 软保护
        task_time_limit=300,
        task_soft_time_limit=240,
        task_always_eager=settings.celery_task_always_eager,
        task_eager_propagates=False,
        imports=("app.resumes.tasks",),
        broker_connection_retry_on_startup=True,
    )
    return celery


celery_app = create_celery_app()


def dispatch_task(task: Task, *args: Any) -> None:
    """入队任务；eager 配置下同步执行（走真实任务调用路径 ``apply``）。

    运行时读取配置（而非仅依赖 import 时的 conf），保证测试环境切换生效。
    """
    if get_settings().celery_task_always_eager:
        task.apply(args=args)
    else:
        task.delay(*args)
