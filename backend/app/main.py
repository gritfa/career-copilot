"""FastAPI 应用入口：日志、request_id 中间件、统一错误处理、健康端点与 /api/v1 路由。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.admin.router import router as admin_router
from app.agents.router import router as agents_router
from app.api.health import router as health_router
from app.auth.router import router as auth_router
from app.consents.router import router as consents_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestIDMiddleware, configure_logging
from app.core.redis import close_redis
from app.db.session import dispose_engine
from app.jobs.router import router as jobs_router
from app.matching.router import router as recommendations_router
from app.privacy.router import router as privacy_router
from app.resumes.facts_router import router as facts_router
from app.resumes.router import router as resumes_router
from app.search_plans.router import router as search_plans_router
from app.tailoring.router import router as tailoring_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """生命周期：引擎/Redis 懒创建，这里只负责收尾释放。"""
    yield
    await dispose_engine(app)
    await close_redis(app)


def create_app() -> FastAPI:
    """应用工厂。"""
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="CareerCopilot API",
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.add_middleware(RequestIDMiddleware)
    # 浏览器前端（Next.js，localhost:3000）跨端口调用 API：只放行配置的前端来源并
    # 允许携带会话 cookie（阶段 9 验收发现缺失，浏览器端登录会被 CORS 拦截）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_base_url.rstrip("/")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    register_exception_handlers(app)
    app.include_router(health_router)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(auth_router)
    api_v1.include_router(consents_router)
    api_v1.include_router(privacy_router)
    api_v1.include_router(resumes_router)
    api_v1.include_router(facts_router)
    api_v1.include_router(search_plans_router)
    api_v1.include_router(jobs_router)
    api_v1.include_router(recommendations_router)
    api_v1.include_router(agents_router)
    api_v1.include_router(tailoring_router)
    api_v1.include_router(admin_router)
    app.include_router(api_v1)
    return app


app = create_app()
