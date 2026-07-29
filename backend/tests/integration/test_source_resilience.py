"""来源韧性集成测试：限速令牌桶、连续失败熔断、验证码/禁止访问立即熔断、
任一条目失败时 run 状态不得为 success。"""

import asyncio

import redis as redis_sync
from sqlalchemy import select

from app.jobs.adapters.base import (
    CaptchaRequiredError,
    RawJobSnapshot,
    SourceJobRef,
    TemporarySourceError,
)
from app.jobs.adapters.fixture_company import fixture_adapter_a
from app.jobs.ratelimit import acquire_source_token
from app.jobs.tasks import sync_source_task
from tests.integration.conftest import TEST_REDIS_URL


async def run_source(source_key: str, run_key: str | None = None):
    return await asyncio.to_thread(
        lambda: sync_source_task.apply(args=(source_key, run_key)).result
    )


async def get_source_and_runs(db_factory, source_key: str):
    from app.db.models import JobSource, SourceRun

    async with db_factory() as db:
        source = (
            await db.execute(select(JobSource).where(JobSource.source_key == source_key))
        ).scalar_one()
        runs = (
            (
                await db.execute(
                    select(SourceRun)
                    .where(SourceRun.job_source_id == source.id)
                    .order_by(SourceRun.started_at)
                )
            )
            .scalars()
            .all()
        )
    return source, runs


# ---------------- 故障注入 Adapter ----------------


class FlakyAdapter:
    """部分条目临时失败：验证 partial_failure 且 run 不绿。"""

    def __init__(self, fail_job_ids: set[str]) -> None:
        self._inner = fixture_adapter_a()
        self.source_key = self._inner.source_key
        self._fail_job_ids = fail_job_ids

    async def discover(self, cursor):
        return await self._inner.discover(cursor)

    async def fetch_detail(self, ref: SourceJobRef) -> RawJobSnapshot:
        if ref.source_job_id in self._fail_job_ids:
            raise TemporarySourceError(f"synthetic 5xx for {ref.source_job_id}")
        return await self._inner.fetch_detail(ref)

    async def check_validity(self, ref):
        return await self._inner.check_validity(ref)

    def policy(self):
        return self._inner.policy()


class AllFailAdapter(FlakyAdapter):
    def __init__(self) -> None:
        super().__init__(fail_job_ids=set())

    async def fetch_detail(self, ref: SourceJobRef) -> RawJobSnapshot:
        raise TemporarySourceError("synthetic outage")


class CaptchaAdapter(FlakyAdapter):
    def __init__(self) -> None:
        super().__init__(fail_job_ids=set())

    async def fetch_detail(self, ref: SourceJobRef) -> RawJobSnapshot:
        raise CaptchaRequiredError("synthetic captcha wall")


class ForbiddenDiscoverAdapter(FlakyAdapter):
    def __init__(self) -> None:
        super().__init__(fail_job_ids=set())

    async def discover(self, cursor):
        from app.jobs.adapters.base import AccessForbiddenError

        raise AccessForbiddenError("synthetic 403 on listing")


# ---------------- 限速令牌桶 ----------------


async def test_token_bucket_rate_limit(real_env):
    client = redis_sync.Redis.from_url(TEST_REDIS_URL)
    try:
        base = 1_000_000.0
        # burst=3：前 3 个令牌立即可得，第 4 个被限速
        for _ in range(3):
            assert acquire_source_token(
                client, "rl_test", per_minute=60, burst=3, now=base
            )
        assert not acquire_source_token(client, "rl_test", per_minute=60, burst=3, now=base)
        # 1 秒后按 60/min 补充 1 个令牌
        assert acquire_source_token(
            client, "rl_test", per_minute=60, burst=3, now=base + 1.0
        )
        assert not acquire_source_token(
            client, "rl_test", per_minute=60, burst=3, now=base + 1.0
        )
    finally:
        client.close()


# ---------------- run 失败不绿 ----------------


async def test_partial_failure_run_is_never_success(app, db_factory, monkeypatch):
    monkeypatch.setattr(
        "app.jobs.tasks.get_adapter", lambda key: FlakyAdapter({"XL-003", "XL-005"})
    )
    status = await run_source("fixture_a")
    assert status == "partial_failure"

    source, runs = await get_source_and_runs(db_factory, "fixture_a")
    run = runs[-1]
    assert run.status != "success", "有条目失败时 run 状态不得为 success"
    assert run.items_seen == 10 and run.items_failed == 2 and run.items_new == 8
    assert run.error_code == "SOURCE_TEMPORARY_ERROR"
    assert source.status == "enabled"  # 部分失败不熔断


async def test_all_failed_run_marked_failed_with_backoff(app, db_factory, monkeypatch):
    monkeypatch.setattr("app.jobs.tasks.get_adapter", lambda key: AllFailAdapter())
    status = await run_source("fixture_a")
    assert status == "failed"
    source, runs = await get_source_and_runs(db_factory, "fixture_a")
    run = runs[-1]
    assert run.items_failed == run.items_seen == 10
    assert run.next_retry_at is not None  # 有限指数退避
    assert source.consecutive_failures == 1


# ---------------- 熔断 ----------------


async def test_consecutive_failures_open_circuit(app, db_factory, monkeypatch):
    monkeypatch.setattr("app.jobs.tasks.get_adapter", lambda key: AllFailAdapter())
    # 阈值 3（来源配置）：连续 3 次全失败后熔断
    for day in ("20260801", "20260802", "20260803"):
        assert await run_source("fixture_a", day) == "failed"
    source, _ = await get_source_and_runs(db_factory, "fixture_a")
    assert source.status == "circuit_open"
    assert source.consecutive_failures == 3

    # 熔断后：后续运行直接跳过，不再访问来源
    assert await run_source("fixture_a", "20260804") == "skipped:circuit_open"


async def test_captcha_triggers_immediate_circuit_break(app, db_factory, monkeypatch):
    monkeypatch.setattr("app.jobs.tasks.get_adapter", lambda key: CaptchaAdapter())
    status = await run_source("fixture_a")
    assert status == "failed"
    source, runs = await get_source_and_runs(db_factory, "fixture_a")
    assert runs[-1].error_code == "CAPTCHA_REQUIRED"
    # 首次即熔断，无需累计到阈值
    assert source.status == "circuit_open"
    assert source.consecutive_failures == 1


async def test_forbidden_on_discover_triggers_immediate_break(app, db_factory, monkeypatch):
    monkeypatch.setattr(
        "app.jobs.tasks.get_adapter", lambda key: ForbiddenDiscoverAdapter()
    )
    status = await run_source("fixture_a")
    assert status == "failed"
    source, runs = await get_source_and_runs(db_factory, "fixture_a")
    assert runs[-1].error_code == "ACCESS_FORBIDDEN"
    assert source.status == "circuit_open"


async def test_circuit_break_recovers_after_success(app, db_factory, monkeypatch):
    """成功运行清零失败计数（熔断打开需管理员恢复，此处验证计数复位路径）。"""
    monkeypatch.setattr("app.jobs.tasks.get_adapter", lambda key: AllFailAdapter())
    assert await run_source("fixture_a", "20260801") == "failed"
    source, _ = await get_source_and_runs(db_factory, "fixture_a")
    assert source.consecutive_failures == 1

    monkeypatch.setattr("app.jobs.tasks.get_adapter", lambda key: fixture_adapter_a())
    assert await run_source("fixture_a", "20260802") == "success"
    source, _ = await get_source_and_runs(db_factory, "fixture_a")
    assert source.consecutive_failures == 0
    assert source.status == "enabled"


# ---------------- 单来源失败不影响其他来源 ----------------


async def test_one_source_failure_does_not_affect_other(app, db_factory, monkeypatch):
    real_get_adapter = __import__(
        "app.jobs.registry", fromlist=["get_adapter"]
    ).get_adapter

    def selective(key: str):
        if key == "fixture_a":
            return AllFailAdapter()
        return real_get_adapter(key)

    monkeypatch.setattr("app.jobs.tasks.get_adapter", selective)
    assert await run_source("fixture_a") == "failed"
    assert await run_source("fixture_b") == "success"
