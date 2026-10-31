"""平台身份是明确的保留角色成员关系，业务通配/个人授权不能代替。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError
from app.modules.auth.models import Role, Tenant, User, UserRole

PLATFORM_ADMIN = "platform_admin"


async def has_platform_role(session: AsyncSession, user_id: UUID) -> bool:
    stmt = (
        select(UserRole.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == user_id, Role.code == PLATFORM_ADMIN)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def is_platform_admin(session: AsyncSession, user_id: UUID) -> bool:
    stmt = (
        select(UserRole.id)
        .join(Role, Role.id == UserRole.role_id)
        .join(User, User.id == UserRole.user_id)
        .join(Tenant, Tenant.id == User.tenant_id)
        .where(
            User.id == user_id,
            UserRole.tenant_id == User.tenant_id,
            Role.code == PLATFORM_ADMIN,
            Tenant.code == "default",
            Tenant.status == "active",
            Tenant.deleted_at.is_(None),
            User.status == "active",
            User.deleted_at.is_(None),
            User.locked_at.is_(None),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def require_platform_admin(session: AsyncSession, actor_id: UUID | None) -> None:
    if actor_id is None or not await is_platform_admin(session, actor_id):
        raise PermissionDeniedError("此操作仅限平台管理员")


async def protect_platform_user(
    session: AsyncSession, target_id: UUID, actor_id: UUID | None
) -> None:
    if await has_platform_role(session, target_id):
        await require_platform_admin(session, actor_id)


def preserve_platform_membership(before: list[str], after: list[str]) -> None:
    if (PLATFORM_ADMIN in before) != (PLATFORM_ADMIN in after):
        raise PermissionDeniedError("平台管理员身份只能由运维授权，不能在用户角色中增删")
