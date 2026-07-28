"""应用级 Redis 客户端：懒初始化挂在 app.state（限流等场景使用）。"""

import redis.asyncio as aioredis
from fastapi import FastAPI, Request

from app.core.config import get_settings


def get_redis(app: FastAPI) -> aioredis.Redis:
    client: aioredis.Redis | None = getattr(app.state, "redis", None)
    if client is None:
        client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        app.state.redis = client
    return client


async def get_redis_dep(request: Request) -> aioredis.Redis:
    """FastAPI 依赖。"""
    return get_redis(request.app)


async def close_redis(app: FastAPI) -> None:
    client: aioredis.Redis | None = getattr(app.state, "redis", None)
    if client is not None:
        await client.aclose()
        app.state.redis = None
