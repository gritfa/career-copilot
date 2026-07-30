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


def test_privacy_purge_and_cleanup_registered_in_beat():
    """阶段 8：注销硬删扫描 + 过期导出清理必须在 beat 注册（每小时）。"""
    schedule = celery_app.conf.beat_schedule
    assert schedule["hourly-purge-due-accounts"]["task"] == "privacy.purge_due_accounts"
    assert (
        schedule["hourly-cleanup-expired-export-files"]["task"]
        == "privacy.cleanup_expired_export_files"
    )
    assert "privacy.generate_data_export" in celery_app.tasks
    assert "privacy.purge_due_accounts" in celery_app.tasks
    assert "privacy.cleanup_expired_export_files" in celery_app.tasks


def test_all_beat_entries_point_to_registered_tasks():
    """beat 里的每个条目必须指向已注册任务——防止改名/漏 import 后静默不执行。

    注册表由 celery_app 模块导入时的 import_default_modules() 保证填充，
    不依赖其他测试模块先 import 业务代码的副作用（跨平台/收集顺序无关）。
    """
    for entry_name, entry in celery_app.conf.beat_schedule.items():
        assert entry["task"] in celery_app.tasks, (
            f"beat 条目 {entry_name} 指向未注册任务 {entry['task']}"
        )


def test_imports_config_matches_registry():
    """imports 列表中的每个模块都至少注册了一个任务（防僵尸配置项）。"""
    registered_modules = {task.__module__ for name, task in celery_app.tasks.items()
                          if not name.startswith("celery.")}
    for module in celery_app.conf.imports:
        assert module in registered_modules, f"{module} 未注册任何任务"
