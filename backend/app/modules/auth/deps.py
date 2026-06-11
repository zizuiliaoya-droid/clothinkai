"""auth 模块的 FastAPI 依赖（鉴权 + 权限加载）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_bypass_session, get_session
from app.core.exceptions import (
    AccountDisabledError,
    AccountLockedError,
    PasswordMustChangeError,
    PermissionDeniedError,
    TokenInvalidError,
)
from app.core.security.auth import decode_token, is_revoked
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.service import AuthService

_bearer_scheme = HTTPBearer(auto_error=False)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
BypassSessionDep = Annotated[AsyncSession, Depends(get_bypass_session)]
BearerDep = Annotated[
    HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
]


async def get_current_user(
    creds: BearerDep,
    session: SessionDep,
) -> User:
    """完整鉴权：验签 + 黑名单 + 状态 + pwd_iat 安全戳。"""
    if creds is None:
        raise TokenInvalidError("缺少 Authorization Bearer token")
    payload = decode_token(creds.credentials, expected_type="access")
    jti = payload.get("jti")
    if jti and await is_revoked(str(jti)):
        raise TokenInvalidError("Token 已被吊销")

    sub = payload.get("sub")
    if not sub:
        raise TokenInvalidError("Token 缺失 sub")
    try:
        user_id = UUID(str(sub))
    except (TypeError, ValueError) as exc:
        raise TokenInvalidError("Token sub 格式错误") from exc

    user = await UserRepository(session).get_by_id(user_id)
    if user is None or user.deleted_at is not None:
        raise TokenInvalidError("用户不存在或已删除")
    if user.status == "disabled":
        raise AccountDisabledError()
    if user.locked_at is not None:
        raise AccountLockedError()

    # pwd_iat 安全戳比对（BR-TKN-004）
    token_pwd_iat = payload.get("pwd_iat")
    if token_pwd_iat != user.password_changed_at.isoformat():
        raise TokenInvalidError("Token 已因密码/角色/权限变更而失效")

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_user_active(user: CurrentUser) -> User:
    """要求用户已强制改密（封锁未改密用户的非密码 API）。"""
    if user.password_must_change:
        raise PasswordMustChangeError()
    return user


CurrentActiveUser = Annotated[User, Depends(get_current_user_active)]


async def get_current_perms(
    user: CurrentActiveUser,
    session: SessionDep,
) -> EffectivePermissions:
    """加载当前用户的有效权限（带 Redis 缓存）。"""
    auth_service = AuthService(session)
    return await auth_service.load_effective_permissions(user.id)


CurrentPerms = Annotated[EffectivePermissions, Depends(get_current_perms)]


def require_permission(scope: str, action: str = "read") -> Depends:  # type: ignore[valid-type]
    """生成"要求权限"的依赖。"""

    async def _checker(perms: CurrentPerms) -> EffectivePermissions:
        if not perms.has(scope, action):
            raise PermissionDeniedError(
                f"缺少权限 {scope}:{action}",
                details={"required_scope": scope, "required_action": action},
            )
        return perms

    return Depends(_checker)
