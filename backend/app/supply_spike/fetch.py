"""Spike 专用受限 HTTP 客户端。

合规硬边界（代码级强制）：
- 单域名 ≤1 请求/2 秒（monotonic 时钟，慢化不可关闭）。
- 可识别 UA、超时、重试上限、连续失败熔断、run 内 URL 缓存（绝不重复打点）。
- 不带 cookie 会话、不模拟登录、不执行 JS，纯只读 GET。
"""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int | None
    text: str
    content_hash: str  # sha256（失败时为空串）
    fetched_at: float
    failure_type: str | None  # None | timeout | transport_error | http_{code} | circuit_open
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return self.failure_type is None and self.status_code is not None


class DomainRateLimiter:
    """每域名最小间隔限速；clock/sleep 可注入（测试用假时钟，不真等待）。"""

    def __init__(
        self,
        min_interval: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if min_interval < 2.0:
            raise ValueError("合规硬边界：单域名限速不得低于 1 请求/2 秒")
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}

    def wait(self, domain: str) -> None:
        now = self._clock()
        last = self._last.get(domain)
        if last is not None:
            remaining = self._min_interval - (now - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last[domain] = self._clock()


@dataclass
class SpikeFetcher:
    """限速 + 重试 + 熔断 + 缓存的只读 GET 客户端。"""

    user_agent: str
    limiter: DomainRateLimiter
    timeout_seconds: float = 10.0
    max_retries: int = 2
    circuit_break_after_failures: int = 3
    transport: httpx.BaseTransport | None = None  # 测试注入 MockTransport
    request_log: list[str] = field(default_factory=list)
    _cache: dict[str, FetchResult] = field(default_factory=dict)
    _consecutive_failures: dict[str, int] = field(default_factory=dict)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout_seconds,
            follow_redirects=True,
            transport=self.transport,
        )

    def circuit_open(self, domain: str) -> bool:
        return self._consecutive_failures.get(domain, 0) >= self.circuit_break_after_failures

    def get(self, url: str) -> FetchResult:
        if url in self._cache:
            cached = self._cache[url]
            return FetchResult(
                url=cached.url,
                final_url=cached.final_url,
                status_code=cached.status_code,
                text=cached.text,
                content_hash=cached.content_hash,
                fetched_at=cached.fetched_at,
                failure_type=cached.failure_type,
                from_cache=True,
            )
        domain = urlsplit(url).netloc
        if self.circuit_open(domain):
            result = FetchResult(
                url=url,
                final_url=url,
                status_code=None,
                text="",
                content_hash="",
                fetched_at=time.time(),
                failure_type="circuit_open",
            )
            self._cache[url] = result
            return result

        failure: str | None = None
        response: httpx.Response | None = None
        with self._client() as client:
            for _attempt in range(1 + self.max_retries):
                self.limiter.wait(domain)
                self.request_log.append(url)
                try:
                    response = client.get(url)
                except httpx.TimeoutException:
                    failure = "timeout"
                    continue
                except httpx.HTTPError:
                    failure = "transport_error"
                    continue
                failure = None
                if response.status_code >= 500:
                    failure = f"http_{response.status_code}"
                    continue  # 5xx 可重试
                break

        if response is not None and failure is None:
            self._consecutive_failures[domain] = 0
            result = FetchResult(
                url=url,
                final_url=str(response.url),
                status_code=response.status_code,
                text=response.text,
                content_hash=hashlib.sha256(response.content).hexdigest(),
                fetched_at=time.time(),
                failure_type=None,
            )
        else:
            self._consecutive_failures[domain] = self._consecutive_failures.get(domain, 0) + 1
            status_code = response.status_code if response is not None else None
            result = FetchResult(
                url=url,
                final_url=str(response.url) if response is not None else url,
                status_code=status_code,
                text="",
                content_hash="",
                fetched_at=time.time(),
                failure_type=failure or "transport_error",
            )
        self._cache[url] = result
        return result
