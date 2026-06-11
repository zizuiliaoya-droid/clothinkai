"""FastAPI 全局异常处理器。

统一响应格式（与需求文档第 16 节"通用规范"一致）：
    {
        "code": "ERROR_CODE",
        "message": "Human-readable message",
        "details": {...}
    }
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppException, DuplicateResourceError

log = logging.getLogger(__name__)


def _payload(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}}


async def _app_exception_handler(_request: Request, exc: AppException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_payload(exc.code, exc.message, exc.details),
    )


async def _validation_exception_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_payload(
            code="VALIDATION_ERROR",
            message="请求数据校验失败",
            details={"errors": exc.errors()},
        ),
    )


async def _integrity_error_handler(_request: Request, exc: IntegrityError) -> JSONResponse:
    """SQLAlchemy 唯一约束冲突 → 409。"""
    detail = str(getattr(exc.orig, "args", ["unknown integrity error"])[0])
    log.warning("integrity_error", extra={"detail": detail})
    err = DuplicateResourceError(details={"db_detail": detail})
    return JSONResponse(
        status_code=err.status_code,
        content=_payload(err.code, err.message, err.details),
    )


async def _rate_limit_handler(_request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=_payload(
            code="RATE_LIMITED",
            message="请求过于频繁，请稍后重试",
            details={"limit": str(exc.detail)},
        ),
    )


async def _internal_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """兜底 500 处理（生产环境隐藏堆栈）。"""
    log.exception("unhandled_exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content=_payload(
            code="INTERNAL_ERROR",
            message="服务器内部错误",
            details={},
        ),
    )


def register_error_handlers(app: FastAPI) -> None:
    """在 FastAPI 应用上注册全部异常处理器。"""
    app.add_exception_handler(AppException, _app_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(IntegrityError, _integrity_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _internal_error_handler)
