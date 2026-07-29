"""Celery 调度注册测试（离线）：每日来源任务必须在 beat 配置中注册。"""

from app.tasks.celery_app import celery_app


def test_daily_job_source_sync_registered_in_beat():
    schedule = celery_app.conf.beat_schedule
    assert "daily-job-source-sync" in schedule
    entry = schedule["daily-job-source-sync"]
    assert entry["task"] == "jobs.sync_all_sources"
    # crontab：每天一次（docs/06 第 5 节全局每日一次）
    assert entry["schedule"].hour == {3}
    assert entry["schedule"].minute == {30}


def test_job_tasks_registered():
    assert "jobs.sync_source" in celery_app.tasks
    assert "jobs.sync_all_sources" in celery_app.tasks
