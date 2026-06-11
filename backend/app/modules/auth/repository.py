"""auth 模块 Repository（SQLAlchemy 查询封装）。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.auth.models import (
    AuditLog,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
    UserPermissionOverride,
    UserRole,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# UserRepository
# ---------------------------------------------------------------------------


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(User.username == username, User.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        search: str | None = None,
    ) -> tuple[Sequence[User], int]:
        stmt = select(User).where(User.deleted_at.is_(None))
        count_stmt = select(func.count()).select_from(User).where(User.deleted_at.is_(None))
        if status:
            stmt = stmt.where(User.status == status)
            count_stmt = count_stmt.where(User.status == status)
        if search:
            like = f"%{search}%"
            cond = or_(User.username.ilike(like), User.display_name.ilike(like))
            stmt = stmt.where(cond)
            count_stmt = count_stmt.where(cond)

        stmt = stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        items = (await self._session.execute(stmt)).scalars().all()
        total = (await self._session.execute(count_stmt)).scalar_one()
        return items, int(total)

    async def add(self, user: User) -> User:
        self._session.add(user)
        await self._session.flush()
        return user

    async def save(self, user: User) -> User:
        await self._session.flush()
        return user


# ---------------------------------------------------------------------------
# RoleRepository
# ---------------------------------------------------------------------------


class RoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_code(self, code: str) -> Role | None:
        stmt = select(Role).where(Role.code == code)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_by_codes(self, codes: list[str]) -> Sequence[Role]:
        if not codes:
            return []
        stmt = select(Role).where(Role.code.in_(codes))
        return (await self._session.execute(stmt)).scalars().all()

    async def list_codes_for_user(self, user_id: UUID) -> list[str]:
        stmt = (
            select(Role.code)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]

    async def list_user_ids_by_role_code(self, role_code: str) -> list[UUID]:
        """租户内拥有指定角色 code 的 active 用户 id 列表（U10a 通知目标解析）。

        tenant 隔离由 user_role/user 的 RLS + ORM 上下文保证；显式加 User 状态过滤。
        """
        stmt = (
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .join(User, User.id == UserRole.user_id)
            .where(
                Role.code == role_code,
                User.status == "active",
                User.deleted_at.is_(None),
            )
        )
        result = await self._session.execute(stmt)
        return [row[0] for row in result.all()]


# ---------------------------------------------------------------------------
# UserRoleRepository
# ---------------------------------------------------------------------------


class UserRoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_user(self, user_id: UUID) -> Sequence[UserRole]:
        stmt = select(UserRole).where(UserRole.user_id == user_id)
        return (await self._session.execute(stmt)).scalars().all()

    async def replace_for_user(self, user_id: UUID, role_ids: list[UUID]) -> None:
        """删除全部旧关联 + 插入新关联。"""
        existing = await self.list_for_user(user_id)
        for ur in existing:
            await self._session.delete(ur)
        await self._session.flush()
        for rid in role_ids:
            self._session.add(UserRole(user_id=user_id, role_id=rid))
        await self._session.flush()


# ---------------------------------------------------------------------------
# PermissionRepository
# ---------------------------------------------------------------------------


class PermissionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_scope(self, scope: str) -> Permission | None:
        stmt = select(Permission).where(Permission.scope == scope)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def upsert_override(
        self,
        user_id: UUID,
        permission_id: UUID,
        *,
        effect: str,
        created_by: UUID | None = None,
        reason: str | None = None,
    ) -> None:
        """插入或更新 user_permission_override（U09 自定义权限 grant/revoke）。

        UNIQUE(tenant, user, permission) 冲突时更新 effect/reason（可在 grant/revoke 间切换）。
        tenant_id 由 TenantScopedModel ORM 钩子按上下文自动注入。
        """
        stmt = select(UserPermissionOverride).where(
            UserPermissionOverride.user_id == user_id,
            UserPermissionOverride.permission_id == permission_id,
        )
        existing = (await self._session.execute(stmt)).scalar_one_or_none()
        if existing is not None:
            existing.effect = effect
            existing.reason = reason
            existing.created_by = created_by
        else:
            self._session.add(
                UserPermissionOverride(
                    user_id=user_id,
                    permission_id=permission_id,
                    effect=effect,
                    reason=reason,
                    created_by=created_by,
                )
            )
        await self._session.flush()

    async def list_scopes_for_user(self, user_id: UUID) -> tuple[set[str], set[str], set[str]]:
        """返回 (role_perms, grants, revokes)，用于权限合并算法。"""
        # role_permissions
        role_stmt = (
            select(Permission.scope)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, and_(UserRole.role_id == Role.id, UserRole.user_id == user_id))
        )
        role_scopes = {row[0] for row in (await self._session.execute(role_stmt)).all()}

        # user_permission_override
        ovr_stmt = (
            select(Permission.scope, UserPermissionOverride.effect)
            .join(
                UserPermissionOverride,
                UserPermissionOverride.permission_id == Permission.id,
            )
            .where(UserPermissionOverride.user_id == user_id)
        )
        grants: set[str] = set()
        revokes: set[str] = set()
        for scope, effect in (await self._session.execute(ovr_stmt)).all():
            if effect == "grant":
                grants.add(scope)
            elif effect == "revoke":
                revokes.add(scope)
        return role_scopes, grants, revokes


# ---------------------------------------------------------------------------
# RefreshTokenRepository
# ---------------------------------------------------------------------------


class RefreshTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, token: RefreshToken) -> RefreshToken:
        self._session.add(token)
        await self._session.flush()
        return token

    async def get_by_jti(self, jti: str) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.jti == jti)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def revoke_jti(self, jti: str) -> None:
        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.jti == jti, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_utcnow())
        )

    async def revoke_all_for_user(self, user_id: UUID) -> int:
        result = await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=_utcnow())
        )
        return int(result.rowcount or 0)

    async def cleanup_expired(self) -> int:
        """删除已过期的 refresh_token。供 Celery 任务调用。"""
        from sqlalchemy import delete

        result = await self._session.execute(
            delete(RefreshToken).where(RefreshToken.expires_at < _utcnow())
        )
        return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# AuditLogRepository
# ---------------------------------------------------------------------------


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query(
        self,
        *,
        action: str | None = None,
        resource: str | None = None,
        user_id: UUID | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[Sequence[AuditLog], int]:
        stmt = select(AuditLog)
        count_stmt = select(func.count()).select_from(AuditLog)
        if action:
            stmt = stmt.where(AuditLog.action == action)
            count_stmt = count_stmt.where(AuditLog.action == action)
        if resource:
            stmt = stmt.where(AuditLog.resource == resource)
            count_stmt = count_stmt.where(AuditLog.resource == resource)
        if user_id:
            stmt = stmt.where(AuditLog.user_id == user_id)
            count_stmt = count_stmt.where(AuditLog.user_id == user_id)
        if date_from:
            stmt = stmt.where(AuditLog.created_at >= date_from)
            count_stmt = count_stmt.where(AuditLog.created_at >= date_from)
        if date_to:
            stmt = stmt.where(AuditLog.created_at < date_to)
            count_stmt = count_stmt.where(AuditLog.created_at < date_to)

        stmt = (
            stmt.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await self._session.execute(stmt)).scalars().all()
        total = (await self._session.execute(count_stmt)).scalar_one()
        return items, int(total)


__all__ = [
    "AuditLogRepository",
    "PermissionRepository",
    "RefreshTokenRepository",
    "RoleRepository",
    "UserRepository",
    "UserRoleRepository",
]
