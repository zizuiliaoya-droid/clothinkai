"""审计日志写入（@audit 装饰器 + AuditService.log + ORM 事件钩子注册器）。

业务规则：
- BR-AUDIT-001: 必记录的操作清单（登录/密码改/用户/角色/权限/凭据解密）
- BR-AUDIT-002: append-only（DB 层 REVOKE UPDATE/DELETE）
- BR-AUDIT-003: 1 年后归档到 R2

实现说明：
- audit_log ORM 模型在 modules/auth/models.py 定义
- 这里只提供"写入入口"，业务代码调用 AuditService.log() 或在 API 上加 @audit 装饰器
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import (
    actor_type_ctx,
    request_id_ctx,
    tenant_id_ctx,
    user_id_ctx,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AuditService
# ---------------------------------------------------------------------------


class AuditService:
    """审计日志服务。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def log(
        self,
        action: str,
        *,
        resource: str | None = None,
        resource_id: UUID | str | None = None,
        actor_type: str | None = None,
        user_id: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        purpose: str | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """写一条审计日志。

        若未传 actor_type / user_id / tenant_id，则尝试从 contextvars 读取。
        """
        # 延迟 import 避免循环依赖
        from app.modules.auth.models import AuditLog

        effective_actor = actor_type or actor_type_ctx.get() or "anonymous"
        effective_user_id = user_id or user_id_ctx.get()
        effective_tenant_id = tenant_id_ctx.get()

        entry = AuditLog(
            tenant_id=effective_tenant_id,
            user_id=effective_user_id,
            actor_type=effective_actor,
            action=action,
            resource=resource,
            resource_id=str(resource_id) if resource_id is not None else None,
            before=before,
            after=after,
            purpose=purpose,
            ip=ip,
            user_agent=user_agent,
            request_id=request_id_ctx.get() or None,
        )
        self._session.add(entry)
        # 不立即 commit；由调用方事务统一提交
        log.debug(
            "audit_logged",
            extra={
                "action": action,
                "actor": effective_actor,
                "user_id": str(effective_user_id) if effective_user_id else None,
            },
        )


# ---------------------------------------------------------------------------
# @audit 装饰器（API 层）
# ---------------------------------------------------------------------------


def audit(
    operation: str,
    *,
    resource: str | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """API 装饰器：调用前/后写审计日志。

    使用方式（modules/auth/api.py 等）：
        @router.post("/x")
        @audit("user_create", resource="user")
        async def create_x(...): ...

    实现细节：装饰器只在调用前/后写日志，需要 service 层显式注入 AuditService 来落库；
    本装饰器在 U01 阶段先做日志层 audit（structlog 输出），DB 写入由 service 层显式调用 AuditService.log。
    这种设计避免了装饰器与 session 事务的强耦合。
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            log.info(
                "api_call_audit",
                extra={
                    "operation": operation,
                    "resource": resource,
                    "actor_type": actor_type_ctx.get(),
                    "user_id": str(user_id_ctx.get()) if user_id_ctx.get() else None,
                    "tenant_id": str(tenant_id_ctx.get()) if tenant_id_ctx.get() else None,
                },
            )
            return await func(*args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# ORM 事件钩子注册器（U01 阶段轻量占位）
# ---------------------------------------------------------------------------


def register_audit_listeners(_model: type, _events: list[str]) -> None:
    """监听敏感表的 INSERT/UPDATE 自动写 audit_log。

    U01 阶段以"显式调用 AuditService.log()"为主；本函数为后续单元（如 U12 凭据）预留扩展位。
    """
    # 占位：U12 启用 credential 表后补全
    return
