"""structlog 结构化 JSON 日志 + request_id 中间件。

安全要求：日志不得包含简历/聊天正文、联系方式、token、密钥、签名 URL。
本模块只输出结构化字段（method/path/status/duration/request_id），
业务模块打日志时同样必须遵守该边界。
"""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"


def new_request_id() -> str:
    """生成对外可见的 request_id（错误响应与日志共用）。"""
    return f"req_{uuid.uuid4().hex[:20]}"


def configure_logging(log_level: str = "INFO") -> None:
    """配置 structlog：JSON 输出、ISO 时间戳、contextvars 绑定。"""
    logging.basicConfig(level=log_level.upper(), format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


class RequestIDMiddleware(BaseHTTPMiddleware):
    """为每个请求生成/透传 request_id，绑定到 structlog contextvars，
    写入 ``request.state.request_id`` 并回写响应头。"""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        logger = structlog.get_logger("app.request")
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        response.headers[REQUEST_ID_HEADER] = request_id
        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        return response


def get_request_id(request: Request) -> str:
    """从请求里取 request_id；中间件未运行时兜底生成一个。"""
    return getattr(request.state, "request_id", None) or new_request_id()
