"""TenancyContext 中间件：解析 JWT 写入 contextvars + Sentry tag + structlog bind。

职责说明（nfr-design 1.4 节）：
- 仅写 contextvars 用于日志和 Sentry 标记
- **真正的鉴权**仍由 modules/auth/deps.py 的 Depends(get_current_user) 完成
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sentry_sdk
import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.security.auth import decode_token_unverified
from app.core.tenancy import (
    actor_type_ctx,
    bypass_rls_ctx,
    tenant_id_ctx,
    user_id_ctx,
)


def _extract_bearer_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip() or None
    return None


class TenancyContextMiddleware(BaseHTTPMiddleware):
    """从 JWT 提取 tenant_id / user_id / actor_type 写入 contextvars。

    注意：此中间件**不验签**（性能优化）。真正的鉴权由 Depends 完成。
    本中间件只负责"标记"日志和 Sentry 上下文。
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        token = _extract_bearer_token(request)
        ctx_tokens: list[Any] = []

        if token:
            payload = decode_token_unverified(token)
            if payload:
                tid_str = payload.get("tenant_id")
                uid_str = payload.get("sub")
                actor = payload.get("actor_type", "user")

                try:
                    tid = UUID(tid_str) if tid_str else None
                except (TypeError, ValueError):
                    tid = None
                try:
                    uid = UUID(uid_str) if uid_str else None
                except (TypeError, ValueError):
                    uid = None

                ctx_tokens.append(tenant_id_ctx.set(tid))
                ctx_tokens.append(user_id_ctx.set(uid))
                ctx_tokens.append(actor_type_ctx.set(str(actor)))

                if actor == "platform_admin":
                    ctx_tokens.append(bypass_rls_ctx.set(True))

                # Sentry 标记（不发送 PII）
                sentry_sdk.set_tag("tenant_id", str(tid) if tid else "none")
                sentry_sdk.set_tag("actor_type", str(actor))
                if uid:
                    sentry_sdk.set_user({"id": str(uid)})

                # structlog bind
                structlog.contextvars.bind_contextvars(
                    tenant_id=str(tid) if tid else None,
                    user_id=str(uid) if uid else None,
                    actor_type=str(actor),
                )

        # ASGI 请求级 contextvars 由 Starlette 自动隔离（每个请求独立 task）
        # ctx_tokens 仅用于显式生命周期跟踪，无需手动 reset
        return await call_next(request)
