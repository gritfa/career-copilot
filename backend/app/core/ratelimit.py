"""Redis 固定窗口限流（docs/04 第 11 节：magic link 按邮箱哈希 + IP 桶限流）。"""

import redis.asyncio as aioredis

from app.core.errors import AppError


async def enforce_rate_limit(
    redis: aioredis.Redis,
    *,
    bucket: str,
    limit: int,
    window_seconds: int,
) -> None:
    """固定窗口计数限流；超限抛 429 RATE_LIMITED（带 retry_after）。

    bucket 必须是哈希/伪匿名值（如邮箱哈希、IP 哈希），不得含邮箱明文。
    """
    key = f"rl:{bucket}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, window_seconds)
    if count > limit:
        ttl = await redis.ttl(key)
        retry_after = ttl if ttl and ttl > 0 else window_seconds
        raise AppError(
            code="RATE_LIMITED",
            message="请求过于频繁，请稍后再试",
            status_code=429,
            details={"retry_after": retry_after},
        )
