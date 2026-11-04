"""平台只读排查：短时独立 token、真实操作者校验及单独审计。"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.config import settings
from app.core.db import AsyncSessionBypass
from app.core.exceptions import PermissionDeniedError, ResourceNotFoundError, TokenInvalidError
from app.core.security.auth import decode_token, is_revoked
from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import bypass_rls_ctx, support_actor_revokes_ctx, support_readonly_ctx
from app.modules.auth.models import Role, Tenant, User, UserRole
from app.modules.auth.platform_access import has_platform_role, is_platform_admin

SUPPORT_SECONDS = 900


@asynccontextmanager
async def support_auth_session():
    """只用于精确身份核对/审计；退出前恢复业务只读和 RLS，不交给业务接口。"""
    read_token = support_readonly_ctx.set(False)
    bypass_token = bypass_rls_ctx.set(True)
    try:
        async with AsyncSessionBypass() as session:
            yield session
    finally:
        bypass_rls_ctx.reset(bypass_token)
        support_readonly_ctx.reset(read_token)


async def _authorize_actor(session: AsyncSession, actor: User | None) -> EffectivePermissions:
    from app.modules.auth.service import AuthService

    if (
        actor is None
        or actor.password_must_change
        or not await is_platform_admin(session, actor.id)
    ):
        raise TokenInvalidError("平台身份已失效", code="SUPPORT_SESSION_INVALID")
    permissions = await AuthService(session).load_effective_permissions(actor.id)
    if not permissions.has("auth.user", "read"):
        raise PermissionDeniedError("平台只读排查权限已失效", code="SUPPORT_SESSION_INVALID")
    return permissions


def _tenant_view(tenant: Tenant) -> dict[str, str]:
    return {"id": str(tenant.id), "code": tenant.code, "name": tenant.name}


async def issue_support_token(
    session: AsyncSession, actor: User, tenant_id: UUID
) -> dict[str, Any]:
    await _authorize_actor(session, actor)
    tenant = (
        await session.execute(
            select(Tenant).where(
                Tenant.id == tenant_id, Tenant.status == "active", Tenant.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if tenant is None:
        raise ResourceNotFoundError("客户租户不存在或已停用")
    platform_member = (
        select(UserRole.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == User.id, Role.code == "platform_admin")
        .correlate(User)
        .exists()
    )
    customer = (
        await session.execute(
            select(User)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                User.tenant_id == tenant_id,
                UserRole.tenant_id == tenant_id,
                Role.code == "admin",
                User.status == "active",
                User.locked_at.is_(None),
                User.deleted_at.is_(None),
                ~platform_member,
            )
            .order_by(User.created_at, User.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if customer is None:
        raise ResourceNotFoundError("客户暂无可用于只读排查的有效业务管理员")
    now = datetime.now(UTC)
    payload = {
        "typ": "support",
        "sub": str(customer.id),
        "tenant_id": str(tenant.id),
        "pwd_iat": customer.password_changed_at.isoformat(),
        "platform_actor_id": str(actor.id),
        "actor_pwd_iat": actor.password_changed_at.isoformat(),
        "actor_type": "support",
        "iat": now,
        "exp": now + timedelta(seconds=SUPPORT_SECONDS),
        "jti": uuid4().hex,
    }
    encoded = jwt.encode(
        payload, settings.JWT_SECRET.get_secret_value(), algorithm=settings.JWT_ALGORITHM
    )
    await AuditService(session).log(
        action="support_session_start",
        actor_type="platform_support",
        user_id=actor.id,
        tenant_id=tenant.id,
        resource="tenant",
        resource_id=tenant.id,
        after={"customer_user_id": str(customer.id), "expires_in": SUPPORT_SECONDS},
    )
    await session.commit()
    return {
        "access_token": encoded,
        "token_type": "bearer",
        "expires_in": SUPPORT_SECONDS,
        "tenant": _tenant_view(tenant),
    }


async def validate_support_identity(
    session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        actor_id, customer_id, tenant_id = (
            UUID(str(payload[key])) for key in ("platform_actor_id", "sub", "tenant_id")
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenInvalidError("只读排查身份不完整", code="SUPPORT_SESSION_INVALID") from exc
    actor = await session.get(User, actor_id)
    actor_permissions = await _authorize_actor(session, actor)
    assert actor is not None
    if actor.password_changed_at.isoformat() != payload.get("actor_pwd_iat"):
        raise TokenInvalidError("平台账号已变更，请重新进入排查", code="SUPPORT_SESSION_INVALID")
    customer = await session.get(User, customer_id)
    tenant = await session.get(Tenant, tenant_id)
    if (
        tenant is None
        or tenant.deleted_at is not None
        or tenant.status != "active"
        or customer is None
        or customer.tenant_id != tenant_id
        or customer.deleted_at is not None
        or customer.status != "active"
        or customer.locked_at is not None
        or customer.password_changed_at.isoformat() != payload.get("pwd_iat")
        or await has_platform_role(session, customer_id)
    ):
        raise TokenInvalidError("客户排查上下文已失效", code="SUPPORT_SESSION_INVALID")
    admin_membership = (
        await session.execute(
            select(UserRole.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id == customer_id,
                UserRole.tenant_id == tenant_id,
                Role.code == "admin",
            )
        )
    ).scalar_one_or_none()
    if admin_membership is None:
        raise TokenInvalidError("客户管理员身份已变更", code="SUPPORT_SESSION_INVALID")
    return {
        "_actor_revokes": sorted(actor_permissions.revokes),
        "tenant": _tenant_view(tenant),
        "actor": {"id": str(actor.id), "username": actor.username},
        "expires_at": datetime.fromtimestamp(payload["exp"], UTC).isoformat(),
    }


async def validate_support_request(
    token: str, method: str, path: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.modules.auth.support_policy import support_read_allowed

    payload = decode_token(token, expected_type="support")
    if not support_read_allowed(method, path):
        raise PermissionDeniedError("只读排查不支持此操作", code="SUPPORT_READ_ONLY")
    if not payload.get("jti") or await is_revoked(str(payload["jti"])):
        raise TokenInvalidError("只读排查已失效", code="SUPPORT_SESSION_INVALID")
    async with support_auth_session() as session:
        context = await validate_support_identity(session, payload)
        actor_revokes = frozenset(context.pop("_actor_revokes"))
        await AuditService(session).log(
            action="support_view",
            actor_type="platform_support",
            user_id=UUID(context["actor"]["id"]),
            tenant_id=UUID(context["tenant"]["id"]),
            resource="tenant",
            resource_id=context["tenant"]["id"],
            after={"method": method, "path": path, "customer_user_id": payload["sub"]},
        )
        await session.commit()
    support_actor_revokes_ctx.set(actor_revokes)
    return payload, context
