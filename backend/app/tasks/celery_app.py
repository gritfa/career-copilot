"""Celery 应用：broker/backend 用 Redis（docs/02 架构）。

- 任务必须幂等、有限重试、可观察；不可重试错误不得无限重放。
- 测试用 CELERY_TASK_ALWAYS_EAGER=1（dispatch_task 同步 apply），
  任务代码本身仍是真实 Celery 任务，worker 部署方式不变：
  ``celery -A app.tasks.celery_app worker``
"""

from typing import Any

from celery import Celery, Task
from celery.schedules import crontab

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
        imports=(
            "app.resumes.tasks",
            "app.jobs.tasks",
            "app.matching.tasks",
            "app.agents.tasks",
            "app.tailoring.tasks",
            "app.privacy.tasks",
        ),
        broker_connection_retry_on_startup=True,
        # 岗位来源：全局每天一次（docs/06 第 5 节）；各来源在任务内随机抖动错峰
        beat_schedule={
            "daily-job-source-sync": {
                "task": "jobs.sync_all_sources",
                "schedule": crontab(hour=3, minute=30),
            },
            # 每日推荐：跟随岗位同步之后（同步 3:30 + 抖动最长 30 分钟）
            "daily-generate-recommendations": {
                "task": "matching.generate_all_recommendations",
                "schedule": crontab(hour=4, minute=30),
            },
            # 注销硬删：每小时扫描宽限期到期账号（失败下轮自动重试）
            "hourly-purge-due-accounts": {
                "task": "privacy.purge_due_accounts",
                "schedule": crontab(minute=10),
            },
            # 过期导出文件清理（数据导出 ZIP + 简历导出）：短时下载、过期删除
            "hourly-cleanup-expired-export-files": {
                "task": "privacy.cleanup_expired_export_files",
                "schedule": crontab(minute=40),
            },
        },
    )
    return celery


celery_app = create_celery_app()

# 任务注册显式确定化（阶段 11 P-1 第 4 项）：conf.imports 只在 worker 启动时
# 由 loader 导入；单独 import 本模块（如 test_celery_beat 先于其他测试收集、
# Windows/pytest 随机顺序）时注册表为空，registration 断言会假失败。
# 这里在模块导入时同步导入同一份 imports 列表——与 worker 启动路径完全一致，
# 任何进程只要 import 了 celery_app 就拿到完整任务注册表，不依赖收集顺序。
celery_app.loader.import_default_modules()


def dispatch_task(task: Task, *args: Any) -> None:
    """入队任务；eager 配置下同步执行（走真实任务调用路径 ``apply``）。

    运行时读取配置（而非仅依赖 import 时的 conf），保证测试环境切换生效。
    """
    if get_settings().celery_task_always_eager:
        task.apply(args=args)
    else:
        task.delay(*args)
