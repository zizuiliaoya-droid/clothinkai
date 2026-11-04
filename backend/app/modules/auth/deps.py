"""auth 模块的 FastAPI 依赖（鉴权 + 权限加载）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, params
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
from app.core.tenancy import (
    request_method_ctx,
    request_path_ctx,
    support_context_ctx,
    support_readonly_ctx,
)
from app.modules.auth.models import User
from app.modules.auth.platform_access import is_platform_admin
from app.modules.auth.repository import PermissionRepository, TenantRepository, UserRepository
from app.modules.auth.service import AuthService

_bearer_scheme = HTTPBearer(auto_error=False)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
BypassSessionDep = Annotated[AsyncSession, Depends(get_bypass_session)]
BearerDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


async def get_current_user(
    creds: BearerDep,
    session: SessionDep,
) -> User:
    """完整鉴权：验签 + 黑名单 + 状态 + pwd_iat 安全戳。"""
    if creds is None:
        raise TokenInvalidError("缺少 Authorization Bearer token")
    if support_readonly_ctx.get():
        from app.modules.auth.support_access import validate_support_request

        payload, context = await validate_support_request(
            creds.credentials, request_method_ctx.get(), request_path_ctx.get()
        )
        support_context_ctx.set(context)
    else:
        payload = decode_token(creds.credentials, expected_type="access")
    jti = payload.get("jti")
    if not support_readonly_ctx.get() and jti and await is_revoked(str(jti)):
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
    if str(user.tenant_id) != payload.get("tenant_id"):
        raise TokenInvalidError("Token 租户与账号不符")
    if not await TenantRepository(session).is_active(user.tenant_id):
        raise TokenInvalidError("租户不可用")
    if user.status == "disabled":
        raise AccountDisabledError()
    if user.locked_at is not None:
        raise AccountLockedError()

    # pwd_iat 安全戳比对（BR-TKN-004）
    token_pwd_iat = payload.get("pwd_iat")
    if token_pwd_iat != user.password_changed_at.isoformat():
        raise TokenInvalidError("Token 已因密码/角色/权限变更而失效")

    if (
        not support_readonly_ctx.get()
        and request_path_ctx.get()
        and await is_platform_admin(session, user.id)
    ):
        from app.modules.auth.support_policy import platform_request_allowed

        if not platform_request_allowed(request_method_ctx.get(), request_path_ctx.get()):
            raise PermissionDeniedError(
                "平台账号请先选择客户进入只读排查", code="PLATFORM_CONSOLE_ONLY"
            )

    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_user_active(user: CurrentUser) -> User:
    """要求用户已强制改密（封锁未改密用户的非密码 API）。"""
    if user.password_must_change and not support_context_ctx.get():
        raise PasswordMustChangeError()
    return user


CurrentActiveUser = Annotated[User, Depends(get_current_user_active)]


async def get_current_perms(
    user: CurrentActiveUser,
    session: SessionDep,
) -> EffectivePermissions:
    """加载当前用户的有效权限（带 Redis 缓存）。"""
    auth_service = AuthService(session)
    permissions = await auth_service.load_effective_permissions(user.id)
    if support_context_ctx.get():
        # 不返回 * 或写能力；字段读取仍沿用目标管理员的字段权限。
        scopes = frozenset(
            p.scope
            for p in await PermissionRepository(session).list_registered()
            if ":" in p.scope
            and p.scope.rsplit(":", 1)[1] in {"read", "read_own", "read_all"}
            and permissions.has(*p.scope.rsplit(":", 1))
        )
        return EffectivePermissions(str(user.id), scopes, permissions.revokes)
    return permissions


async def require_permission_catalog_read(
    user: CurrentActiveUser,
    session: SessionDep,
) -> None:
    """目录/详情原本要求 grant；排查只开放这两个 GET，实际写入仍全部拒绝。"""
    permissions = await AuthService(session).load_effective_permissions(user.id)
    if not permissions.has("auth.permission", "grant"):
        raise PermissionDeniedError("缺少个人权限查看权限")


CurrentPerms = Annotated[EffectivePermissions, Depends(get_current_perms)]


def require_permission(scope: str, action: str = "read") -> params.Depends:
    """生成"要求权限"的依赖。"""

    async def _checker(perms: CurrentPerms) -> EffectivePermissions:
        if not perms.has(scope, action):
            raise PermissionDeniedError(
                f"缺少权限 {scope}:{action}",
                details={"required_scope": scope, "required_action": action},
            )
        return perms

    # 直接构造 params.Depends：fastapi.Depends() 的返回注解是 Any，
    # 用它会让本函数变成「返回 Any」。两者等价（Depends() 内部就是这一句）。
    return params.Depends(dependency=_checker)


def require_any_permission(*pairs: tuple[str, str]) -> params.Depends:
    """生成"任一 (scope, action) 命中即过"的依赖（如工作进度 read / read_own）。

    只挡「一个都没有」；持哪一个、能看多少由 service 层再判（report/pr_scope.py）。
    """

    async def _checker(perms: CurrentPerms) -> EffectivePermissions:
        if not any(perms.has(scope, action) for scope, action in pairs):
            raise PermissionDeniedError(
                "缺少权限 " + " / ".join(f"{scope}:{action}" for scope, action in pairs),
                details={
                    "required_any": [
                        {"required_scope": scope, "required_action": action}
                        for scope, action in pairs
                    ]
                },
            )
        return perms

    return params.Depends(dependency=_checker)
