"""RequestId 中间件：给每个请求分配 ID 写入 contextvars 和响应头。"""

from __future__ import annotations

import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.tenancy import request_id_ctx


class RequestIdMiddleware(BaseHTTPMiddleware):
    """读取或生成 X-Request-ID，写入 contextvars + structlog + 响应头。"""

    HEADER_NAME = "X-Request-ID"

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        rid = request.headers.get(self.HEADER_NAME) or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        structlog.contextvars.bind_contextvars(request_id=rid)
        try:
            response = await call_next(request)
            response.headers[self.HEADER_NAME] = rid
            return response
        finally:
            request_id_ctx.reset(token)
            structlog.contextvars.clear_contextvars()
