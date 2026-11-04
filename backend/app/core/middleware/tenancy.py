"""TenancyContext 中间件：解析 JWT 写入 contextvars + Sentry tag + structlog bind。

职责说明（nfr-design 1.4 节）：
- 仅写 contextvars 用于日志和 Sentry 标记
- 真正的身份鉴权仍由 Depends(get_current_user) 完成；support token 先验签并限制 HTTP 范围
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sentry_sdk
import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.exceptions import AppException, PermissionDeniedError
from app.core.security.auth import decode_token, decode_token_unverified
from app.core.tenancy import (
    actor_type_ctx,
    bypass_rls_ctx,
    request_method_ctx,
    request_path_ctx,
    support_actor_revokes_ctx,
    support_context_ctx,
    support_readonly_ctx,
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

    普通 token 这里只提取标签，身份由 Depends 验证；support token 还会验签并挡住非白名单请求。
    actor_type 不能授予 bypass；所有请求结束后显式恢复上下文。
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        token = _extract_bearer_token(request)
        ctx_tokens: list[Any] = [
            tenant_id_ctx.set(None),
            user_id_ctx.set(None),
            actor_type_ctx.set("anonymous"),
            bypass_rls_ctx.set(False),
            support_readonly_ctx.set(False),
            support_context_ctx.set(None),
            support_actor_revokes_ctx.set(frozenset()),
            request_path_ctx.set(request.url.path),
            request_method_ctx.set(request.method),
        ]

        try:
            return await self._dispatch_with_context(request, call_next, token, ctx_tokens)
        finally:
            for ctx_token in reversed(ctx_tokens):
                ctx_token.var.reset(ctx_token)

    async def _dispatch_with_context(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
        token: str | None,
        ctx_tokens: list[Any],
    ) -> Response:
        if token:
            payload = decode_token_unverified(token)
            if payload:
                if payload.get("typ") == "support":
                    from app.modules.auth.support_policy import support_read_allowed

                    try:
                        payload = decode_token(token, expected_type="support")
                        if not support_read_allowed(request.method, request.url.path):
                            raise PermissionDeniedError(
                                "只读排查不支持此操作", code="SUPPORT_READ_ONLY"
                            )
                    except AppException as exc:
                        return JSONResponse(
                            status_code=exc.status_code,
                            content={
                                "code": exc.code,
                                "message": exc.message,
                                "details": exc.details,
                            },
                        )
                    ctx_tokens.append(support_readonly_ctx.set(True))
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

                # JWT 的 actor_type 只是日志标签，不能据此开启跨租户 bypass。

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

        return await call_next(request)
