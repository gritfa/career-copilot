"""来源级限速：Redis 令牌桶（Lua 原子执行，docs/06 第 5 节硬限速）。"""

import time

import redis as redis_sync

# KEYS[1]=桶键；ARGV: rate(令牌/秒), burst, now(秒)
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local data = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(data[1])
local ts = tonumber(data[2])
if tokens == nil then tokens = burst end
if ts == nil then ts = now end
tokens = math.min(burst, tokens + (now - ts) * rate)
local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, 600)
return allowed
"""


def acquire_source_token(
    client: redis_sync.Redis,
    source_key: str,
    *,
    per_minute: int,
    burst: int | None = None,
    now: float | None = None,
) -> bool:
    """尝试取一个令牌；False 表示当前受来源硬限速约束。"""
    if per_minute <= 0:
        return False
    burst_size = burst if burst and burst > 0 else max(1, per_minute // 2)
    result = client.eval(
        _TOKEN_BUCKET_LUA,
        1,
        f"job_source_rl:{source_key}",
        per_minute / 60.0,
        burst_size,
        now if now is not None else time.time(),
    )
    return int(result) == 1


def wait_for_token(
    client: redis_sync.Redis,
    source_key: str,
    *,
    per_minute: int,
    burst: int | None = None,
    max_wait_seconds: float = 10.0,
    poll_interval: float = 0.05,
) -> bool:
    """有界等待令牌；超时返回 False（调用方按限速失败处理，不无限阻塞）。"""
    deadline = time.monotonic() + max_wait_seconds
    while True:
        if acquire_source_token(client, source_key, per_minute=per_minute, burst=burst):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)
