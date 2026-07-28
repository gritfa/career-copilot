"""异步数据库引擎/会话管理：懒初始化挂在 app.state，生命周期结束时释放。"""

from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def get_engine(app: FastAPI) -> AsyncEngine:
    """获取（或懒创建）应用级异步引擎。"""
    engine: AsyncEngine | None = getattr(app.state, "db_engine", None)
    if engine is None:
        engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
        app.state.db_engine = engine
        app.state.db_sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return engine


def get_sessionmaker(app: FastAPI) -> async_sessionmaker[AsyncSession]:
    get_engine(app)
    return app.state.db_sessionmaker


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：请求级数据库会话。"""
    factory = get_sessionmaker(request.app)
    async with factory() as session:
        yield session


async def dispose_engine(app: FastAPI) -> None:
    engine: AsyncEngine | None = getattr(app.state, "db_engine", None)
    if engine is not None:
        await engine.dispose()
        app.state.db_engine = None
        app.state.db_sessionmaker = None
