"""统一错误格式（docs/04-api.md 1.1 节）与异常处理器。

响应体形如::

    {"error": {"code": "...", "message": "...", "request_id": "req_...", "details": {...}}}

不得把供应商异常栈、提示词、密钥、DSN 或正文返回前端。
"""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_request_id

logger = structlog.get_logger("app.errors")

# HTTP 状态码 → 稳定错误码（未列出的走 HTTP_ERROR）
_STATUS_CODE_MAP = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


class AppError(Exception):
    """业务异常基类：携带稳定错误码，供统一处理器输出。"""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """构造统一错误响应体。"""
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status_code, content=body)


def register_exception_handlers(app: FastAPI) -> None:
    """注册统一异常处理器。"""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return error_response(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            request_id=get_request_id(request),
            details=exc.details,
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _STATUS_CODE_MAP.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error"
        return error_response(
            status_code=exc.status_code,
            code=code,
            message=message,
            request_id=get_request_id(request),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 只回传字段位置和错误类型，不回显用户输入内容
        details = {
            "errors": [
                {
                    "loc": [str(part) for part in err.get("loc", [])],
                    "type": err.get("type", "unknown"),
                }
                for err in exc.errors()
            ]
        }
        return error_response(
            status_code=422,
            code="VALIDATION_ERROR",
            message="请求参数校验失败",
            request_id=get_request_id(request),
            details=details,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # 完整异常只进结构化日志（不含请求正文），响应体不泄漏内部细节
        logger.error(
            "unhandled_exception",
            exc_type=type(exc).__name__,
            path=request.url.path,
        )
        return error_response(
            status_code=500,
            code="INTERNAL_ERROR",
            message="服务器内部错误",
            request_id=get_request_id(request),
        )
