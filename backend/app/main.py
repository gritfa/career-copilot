"""FastAPI 应用入口：日志、request_id 中间件、统一错误处理与健康端点。"""

from fastapi import FastAPI

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import RequestIDMiddleware, configure_logging


def create_app() -> FastAPI:
    """应用工厂。"""
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="CareerCopilot API",
        version="0.1.0",
        debug=settings.debug,
    )
    app.add_middleware(RequestIDMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    return app


app = create_app()
