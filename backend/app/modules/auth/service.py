"""auth 模块业务服务（编排 Repository + Domain）。

实现的业务规则（详见 functional-design/business-rules.md）：
- BR-AUTH-001/002: 双层登录失败处理（IP+username Redis 计数 + 账户级锁定）
- BR-PWD-001/002/004: 密码策略 + bcrypt + 临时密码
- BR-TKN-003/004: Token 校验 + 失效场景（pwd_iat 安全戳 + 黑名单）
- BR-PERM-001: 权限合并算法
- BR-AUDIT-001: 必记录的操作清单
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.cache import cache
from app.core.config import settings
from app.core.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    InvalidCredentialsError,
    PasswordMustChangeError,
    PermissionDeniedError,
    RateLimitedError,
    ResourceNotFoundError,
    TenantContextMissingError,
    TokenInvalidError,
    ValidationError,
)
from app.core.security.auth import (
    decode_token,
    encode_access_token,
    encode_refresh_token,
    hash_password,
    is_revoked,
    revoke_token,
    verify_password,
)
from app.core.security.permissions import (
    EffectivePermissions,
    invalidate_user_permissions_cache,
)
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.domain import generate_random_password, merge_permissions
from app.modules.auth.exceptions import (
    CannotUnlockUserError,
    RoleNotFoundError,
    UsernameAlreadyExistsError,
)
from app.modules.auth.models import (
    RefreshToken,
    Role,
    User,
)
from app.modules.auth.repository import (
    AuditLogRepository,
    PermissionRepository,
    RefreshTokenRepository,
    RoleRepository,
    UserRepository,
    UserRoleRepository,
)
from app.modules.auth.schemas import (
    AuditLogQuery,
    LoginRequest,
    UserCreate,
    UserUpdate,
)

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _login_fail_key(ip: str, username: str) -> str:
    return f"login:fail:{ip}:{username}"


# ---------------------------------------------------------------------------
# AuthService（登录 / 刷新 / 修改密码）
# ---------------------------------------------------------------------------


class AuthService:
    """认证服务。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._roles = RoleRepository(session)
        self._tokens = RefreshTokenRepository(session)
        self._audit = AuditService(session)
        self._permissions = PermissionRepository(session)

    # ------------------------------------------------------------------ #
    # 登录
    # ------------------------------------------------------------------ #
    async def login(
        self,
        payload: LoginRequest,
        *,
        ip: str,
        user_agent: str | None = None,
    ) -> tuple[str, str, User, bool]:
        """登录（BR-AUTH-001/002 双层失败处理）。

        返回：(access_token, refresh_token, user, must_change_password)
        """
        username = payload.username
        fail_key = _login_fail_key(ip, username)

        # L3: (IP, username) 维度限流
        fail_count = int(await cache.get(fail_key) or 0)
        if fail_count >= settings.LOGIN_FAIL_LIMIT_PER_IP_USERNAME:
            ttl = await cache.ttl(fail_key)
            await self._audit.log(
                action="login_rate_limited",
                actor_type="unknown",
                resource="user",
                purpose=f"ip+username over {settings.LOGIN_FAIL_LIMIT_PER_IP_USERNAME}/15min",
                ip=ip,
                user_agent=user_agent,
            )
            await self._session.commit()
            raise RateLimitedError(retry_after_seconds=max(ttl, 1))

        # 加载用户
        user = await self._users.get_by_username(username)

        # 用户不存在分支（BR-AUTH-003 仍计数，避免用户名探测）
        if user is None:
            await self._record_fail(fail_key)
            await self._audit.log(
                action="login_failed",
                actor_type="unknown",
                resource="user",
                after={"username": username},
                ip=ip,
                user_agent=user_agent,
            )
            await self._session.commit()
            raise InvalidCredentialsError()

        # 锁定 / 禁用 / 软删除
        if user.locked_at is not None:
            await self._audit.log(
                action="login_locked",
                actor_type="user",
                user_id=user.id,
                resource="user",
                resource_id=user.id,
                ip=ip,
                user_agent=user_agent,
            )
            await self._session.commit()
            raise AccountLockedError()
        if user.deleted_at is not None or user.status == "disabled":
            await self._audit.log(
                action="login_disabled",
                actor_type="user",
                user_id=user.id,
                resource="user",
                resource_id=user.id,
                ip=ip,
                user_agent=user_agent,
            )
            await self._session.commit()
            raise AccountDisabledError()

        # 校验密码
        if not verify_password(payload.password, user.password_hash):
            await self._record_fail(fail_key)
            await self._increment_account_failure(user, ip=ip, user_agent=user_agent)
            await self._audit.log(
                action="login_failed",
                actor_type="user",
                user_id=user.id,
                resource="user",
                resource_id=user.id,
                ip=ip,
                user_agent=user_agent,
            )
            await self._session.commit()
            raise InvalidCredentialsError()

        # 成功：清失败计数 + 重置 user.failed_login_count
        await cache.delete(fail_key)
        if user.failed_login_count > 0:
            user.failed_login_count = 0
        user.last_login_at = _utcnow()

        # 签发 token
        roles = await self._roles.list_codes_for_user(user.id)
        access_token, _ = encode_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=roles,
            actor_type="user",
            must_change_password=user.password_must_change,
            pwd_iat=user.password_changed_at,
        )
        refresh, refresh_jti, refresh_exp = encode_refresh_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
        )
        await self._tokens.add(
            RefreshToken(
                tenant_id=user.tenant_id,
                user_id=user.id,
                jti=refresh_jti,
                issued_at=_utcnow(),
                expires_at=refresh_exp,
                user_agent=user_agent,
                ip=ip,
            )
        )
        await self._audit.log(
            action="login",
            actor_type="user",
            user_id=user.id,
            resource="user",
            resource_id=user.id,
            ip=ip,
            user_agent=user_agent,
        )
        await self._session.commit()
        return access_token, refresh, user, user.password_must_change

    async def _record_fail(self, fail_key: str) -> None:
        new_count = await cache.incr(fail_key)
        if new_count == 1:
            await cache.expire(fail_key, settings.LOGIN_FAIL_TTL_SECONDS)

    async def _increment_account_failure(
        self,
        user: User,
        *,
        ip: str,
        user_agent: str | None,
    ) -> None:
        """L4 账户级累计；超过阈值锁账户 + 写 user_lock 审计。"""
        user.failed_login_count += 1
        if user.failed_login_count >= settings.ACCOUNT_LOCK_THRESHOLD:
            user.locked_at = _utcnow()
            # 安全戳：锁定也使现有 token 失效
            user.password_changed_at = _utcnow()
            await self._tokens.revoke_all_for_user(user.id)
            await invalidate_user_permissions_cache(str(user.id))
            await self._audit.log(
                action="user_lock",
                actor_type="system",
                user_id=user.id,
                resource="user",
                resource_id=user.id,
                purpose="exceeded_login_attempts",
                ip=ip,
                user_agent=user_agent,
            )

    # ------------------------------------------------------------------ #
    # 刷新 token
    # ------------------------------------------------------------------ #
    async def refresh(self, refresh_token: str) -> tuple[str, str, User]:
        payload = decode_token(refresh_token, expected_type="refresh")
        jti = payload.get("jti")
        if not jti or await is_revoked(str(jti)):
            raise TokenInvalidError("Token 已吊销")
        token_record = await self._tokens.get_by_jti(str(jti))
        if token_record is None or token_record.revoked_at is not None:
            raise TokenInvalidError("Token 不存在或已吊销")

        user = await self._users.get_by_id(token_record.user_id)
        if user is None or user.deleted_at or user.status != "active" or user.locked_at:
            raise TokenInvalidError("用户状态异常")

        # 滑动续期：吊销旧的，签发新的
        await self._tokens.revoke_jti(str(jti))
        roles = await self._roles.list_codes_for_user(user.id)
        access_token, _ = encode_access_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
            roles=roles,
            actor_type="user",
            must_change_password=user.password_must_change,
            pwd_iat=user.password_changed_at,
        )
        new_refresh, new_jti, new_exp = encode_refresh_token(
            user_id=user.id,
            tenant_id=user.tenant_id,
        )
        await self._tokens.add(
            RefreshToken(
                tenant_id=user.tenant_id,
                user_id=user.id,
                jti=new_jti,
                issued_at=_utcnow(),
                expires_at=new_exp,
            )
        )
        await self._session.commit()
        return access_token, new_refresh, user

    # ------------------------------------------------------------------ #
    # 修改密码
    # ------------------------------------------------------------------ #
    async def change_password(
        self,
        user_id: UUID,
        old_password: str,
        new_password: str,
    ) -> None:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise ResourceNotFoundError("用户不存在")
        if not verify_password(old_password, user.password_hash):
            await self._audit.log(
                action="password_change_failed",
                actor_type="user",
                user_id=user.id,
                resource="user",
                resource_id=user.id,
            )
            await self._session.commit()
            raise InvalidCredentialsError("原密码错误")
        if verify_password(new_password, user.password_hash):
            raise InvalidCredentialsError("新密码不能与当前密码相同")

        user.password_hash = hash_password(new_password)
        user.password_changed_at = _utcnow()  # 安全戳
        user.password_must_change = False
        await self._tokens.revoke_all_for_user(user.id)
        await invalidate_user_permissions_cache(str(user.id))
        await self._audit.log(
            action="password_change",
            actor_type="user",
            user_id=user.id,
            resource="user",
            resource_id=user.id,
        )
        await self._session.commit()

    # ------------------------------------------------------------------ #
    # 加载有效权限（供 deps 使用）
    # ------------------------------------------------------------------ #
    async def load_effective_permissions(self, user_id: UUID) -> EffectivePermissions:
        role_scopes, grants, revokes = await self._permissions.list_scopes_for_user(user_id)
        scopes = merge_permissions(role_scopes, grants, revokes)
        return EffectivePermissions(user_id=str(user_id), scopes=scopes)


# ---------------------------------------------------------------------------
# UserService
# ---------------------------------------------------------------------------


class UserService:
    """用户管理服务。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._roles = RoleRepository(session)
        self._user_roles = UserRoleRepository(session)
        self._tokens = RefreshTokenRepository(session)
        self._audit = AuditService(session)

    async def create(self, payload: UserCreate) -> tuple[User, str]:
        """BR-INIT 创建用户：生成临时密码 + 强制改密 + 角色分配。"""
        existing = await self._users.get_by_username(payload.username)
        if existing is not None:
            raise UsernameAlreadyExistsError()

        # 校验角色存在
        if payload.role_codes:
            roles = await self._roles.list_by_codes(payload.role_codes)
            if len(roles) != len(payload.role_codes):
                missing = set(payload.role_codes) - {r.code for r in roles}
                raise RoleNotFoundError(f"角色不存在: {sorted(missing)}")
        else:
            roles = []

        plain_password = generate_random_password(16)
        tid = tenant_id_ctx.get()
        if tid is None:
            raise TenantContextMissingError()

        user = User(
            tenant_id=tid,
            username=payload.username,
            password_hash=hash_password(plain_password),
            display_name=payload.display_name,
            email=payload.email,
            status="active",
            password_must_change=True,
        )
        await self._users.add(user)
        for r in roles:
            from app.modules.auth.models import UserRole

            self._session.add(UserRole(tenant_id=tid, user_id=user.id, role_id=r.id))
        await self._audit.log(
            action="user_create",
            actor_type="user",
            resource="user",
            resource_id=user.id,
            after={
                "username": user.username,
                "role_codes": [r.code for r in roles],
            },
        )
        await self._session.commit()
        return user, plain_password

    async def update(self, user_id: UUID, payload: UserUpdate) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise ResourceNotFoundError("用户不存在")
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        if payload.display_name is not None and payload.display_name != user.display_name:
            before["display_name"] = user.display_name
            after["display_name"] = payload.display_name
            user.display_name = payload.display_name
        if payload.email is not None and payload.email != user.email:
            before["email"] = user.email
            after["email"] = payload.email
            user.email = payload.email

        if before:  # 仅当真有变更时写 audit
            await self._audit.log(
                action="user_update",
                actor_type="user",
                resource="user",
                resource_id=user.id,
                before=before,
                after=after,
            )
        await self._session.commit()
        return user

    async def toggle_active(self, user_id: UUID) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise ResourceNotFoundError("用户不存在")
        before = {"status": user.status}
        user.status = "disabled" if user.status == "active" else "active"
        # 安全戳：状态变更触发 token 失效
        user.password_changed_at = _utcnow()
        await self._tokens.revoke_all_for_user(user.id)
        await invalidate_user_permissions_cache(str(user.id))
        await self._audit.log(
            action="user_toggle",
            actor_type="user",
            resource="user",
            resource_id=user.id,
            before=before,
            after={"status": user.status},
        )
        await self._session.commit()
        return user

    async def unlock(self, user_id: UUID) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise ResourceNotFoundError("用户不存在")
        if user.locked_at is None:
            raise CannotUnlockUserError()
        user.locked_at = None
        user.failed_login_count = 0
        user.password_changed_at = _utcnow()
        await self._tokens.revoke_all_for_user(user.id)
        await invalidate_user_permissions_cache(str(user.id))
        await self._audit.log(
            action="user_unlock",
            actor_type="user",
            resource="user",
            resource_id=user.id,
        )
        await self._session.commit()
        return user

    async def reset_password(self, user_id: UUID) -> str:
        """管理员重置密码 → 返回临时密码（一次性）+ 强制改密。"""
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise ResourceNotFoundError("用户不存在")
        plain_password = generate_random_password(16)
        user.password_hash = hash_password(plain_password)
        user.password_must_change = True
        user.password_changed_at = _utcnow()
        await self._tokens.revoke_all_for_user(user.id)
        await invalidate_user_permissions_cache(str(user.id))
        await self._audit.log(
            action="password_reset",
            actor_type="user",
            resource="user",
            resource_id=user.id,
            purpose="admin_reset",
        )
        await self._session.commit()
        return plain_password

    async def assign_roles(self, user_id: UUID, role_codes: list[str]) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None:
            raise ResourceNotFoundError("用户不存在")
        roles = await self._roles.list_by_codes(role_codes)
        if len(roles) != len(role_codes):
            missing = set(role_codes) - {r.code for r in roles}
            raise RoleNotFoundError(f"角色不存在: {sorted(missing)}")
        before = await self._roles.list_codes_for_user(user_id)
        await self._user_roles.replace_for_user(user_id, [r.id for r in roles])
        # 安全戳：角色变更触发 token 失效
        user.password_changed_at = _utcnow()
        await self._tokens.revoke_all_for_user(user.id)
        await invalidate_user_permissions_cache(str(user.id))
        await self._audit.log(
            action="role_assign",
            actor_type="user",
            resource="user",
            resource_id=user.id,
            before={"role_codes": before},
            after={"role_codes": [r.code for r in roles]},
        )
        await self._session.commit()
        return user

    async def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
        search: str | None = None,
    ) -> tuple[list[User], int]:
        items, total = await self._users.list(
            page=page, page_size=page_size, status=status, search=search
        )
        return list(items), total


# ---------------------------------------------------------------------------
# PermissionService（U09 EP01-S05 自定义权限 grant/revoke/effective）
# ---------------------------------------------------------------------------


class PermissionService:
    """自定义权限服务：复用 U01 user_permission_override + merge_permissions。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._perms = PermissionRepository(session)
        self._users = UserRepository(session)
        self._audit = AuditService(session)

    async def _ensure_user(self, user_id: UUID) -> User:
        user = await self._users.get_by_id(user_id)
        if user is None or user.deleted_at is not None:
            raise ResourceNotFoundError("用户不存在")
        return user

    async def _resolve_permission_id(self, scope: str) -> UUID:
        perm = await self._perms.get_by_scope(scope)
        if perm is None:
            raise ValidationError(
                f"未知权限 scope: {scope}", details={"scope": scope}
            )
        return perm.id

    async def _apply(
        self,
        target_user_id: UUID,
        scope: str,
        *,
        effect: str,
        actor_id: UUID,
        reason: str | None,
    ) -> None:
        await self._ensure_user(target_user_id)
        permission_id = await self._resolve_permission_id(scope)
        await self._perms.upsert_override(
            target_user_id,
            permission_id,
            effect=effect,
            created_by=actor_id,
            reason=reason,
        )
        await invalidate_user_permissions_cache(str(target_user_id))
        await self._audit.log(
            action=f"permission.{effect}",
            actor_type="user",
            user_id=actor_id,
            resource="user",
            resource_id=target_user_id,
            after={"scope": scope, "effect": effect},
        )
        await self._session.commit()

    async def grant(
        self,
        target_user_id: UUID,
        scope: str,
        *,
        actor_id: UUID,
        reason: str | None = None,
    ) -> None:
        await self._apply(
            target_user_id, scope, effect="grant", actor_id=actor_id, reason=reason
        )

    async def revoke(
        self,
        target_user_id: UUID,
        scope: str,
        *,
        actor_id: UUID,
        reason: str | None = None,
    ) -> None:
        await self._apply(
            target_user_id, scope, effect="revoke", actor_id=actor_id, reason=reason
        )

    async def get_effective(self, target_user_id: UUID) -> dict[str, Any]:
        await self._ensure_user(target_user_id)
        role_scopes, grants, revokes = await self._perms.list_scopes_for_user(
            target_user_id
        )
        effective = merge_permissions(role_scopes, grants, revokes)
        return {
            "user_id": str(target_user_id),
            "role_scopes": sorted(role_scopes),
            "grants": sorted(grants),
            "revokes": sorted(revokes),
            "effective": sorted(effective),
        }


# ---------------------------------------------------------------------------
# AuditQueryService（GET /api/audit-logs）
# ---------------------------------------------------------------------------


class AuditQueryService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = AuditLogRepository(session)

    async def query(self, params: AuditLogQuery) -> tuple[list, int]:
        items, total = await self._repo.query(
            action=params.action,
            resource=params.resource,
            user_id=params.user_id,
            date_from=params.date_from,
            date_to=params.date_to,
            page=params.page,
            page_size=params.page_size,
        )
        return list(items), total


__all__ = [
    "AuditQueryService",
    "AuthService",
    "PermissionService",
    "UserService",
]
