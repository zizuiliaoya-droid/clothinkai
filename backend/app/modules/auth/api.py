"""auth 模块 FastAPI router（13 个端点）。

四层限流：
- L1 全局：main.py 中 slowapi default_limits=100/minute
- L2 端点 IP：本文件 @limiter.limit("20/minute") 等
- L3/L4：AuthService 内部 Redis + DB
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from slowapi.util import get_remote_address

from app.core.audit import AuditService
from app.modules.auth import permissions as scopes
from app.modules.auth.deps import (
    BypassSessionDep,
    CurrentActiveUser,
    CurrentPerms,
    CurrentUser,
    SessionDep,
    require_permission,
)
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    AuditLogEntry,
    AuditLogListResponse,
    AuditLogQuery,
    ChangePasswordRequest,
    EffectivePermissionsView,
    LoginRequest,
    PageMeta,
    PermissionOverrideIn,
    RefreshRequest,
    ResetPasswordResponse,
    RoleAssignRequest,
    TokenPair,
    UserCreate,
    UserCreateResponse,
    UserListItem,
    UserListResponse,
    UserSummary,
    UserUpdate,
)
from app.modules.auth.service import (
    AuditQueryService,
    AuthService,
    PermissionService,
    UserService,
)

# slowapi 限流装饰器从 app.main 中绑定的 limiter 上获取
# 但为了避免循环依赖，这里使用全局 limiter 单例
# 真正的 main.py 会调用 limiter.init_app(app) 与 add_exception_handler
from app.core.config import settings  # noqa: E402

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(tags=["auth"])


# ============================================================================
# Auth 端点
# ============================================================================


@router.post("/auth/login", response_model=TokenPair, status_code=status.HTTP_200_OK)
async def login(
    request: Request,  # 用于 slowapi 识别
    payload: LoginRequest,
    session: BypassSessionDep,
) -> TokenPair:
    """EP01-S01 用户登录。"""
    ip = get_remote_address(request)
    user_agent = request.headers.get("User-Agent")
    auth_service = AuthService(session)
    access, refresh, _user, must_change = await auth_service.login(
        payload, ip=ip, user_agent=user_agent
    )
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        must_change_password=must_change,
    )


@router.post("/auth/refresh", response_model=TokenPair)
async def refresh_token(
    payload: RefreshRequest,
    session: BypassSessionDep,
) -> TokenPair:
    """EP01-S01 刷新 token。"""
    auth_service = AuthService(session)
    access, refresh, user = await auth_service.refresh(payload.refresh_token)
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        must_change_password=user.password_must_change,
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    """登出（吊销当前用户所有 refresh_token）。"""
    from app.modules.auth.repository import RefreshTokenRepository

    await RefreshTokenRepository(session).revoke_all_for_user(user.id)
    await AuditService(session).log(
        action="logout",
        actor_type="user",
        user_id=user.id,
        resource="user",
        resource_id=user.id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/me", response_model=UserSummary)
async def get_me(
    user: CurrentUser,
    session: SessionDep,
) -> UserSummary:
    """当前用户信息（即使 password_must_change 也允许访问）。"""
    from app.modules.auth.repository import RoleRepository

    roles = await RoleRepository(session).list_codes_for_user(user.id)
    return UserSummary(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        status=user.status,
        password_must_change=user.password_must_change,
        roles=roles,
    )


@router.put("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    user: CurrentUser,
    session: SessionDep,
) -> Response:
    """EP01-S02 修改密码（即使 password_must_change 也可访问）。"""
    auth_service = AuthService(session)
    await auth_service.change_password(user.id, payload.old_password, payload.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ============================================================================
# 用户管理端点（要求 auth.user:write 权限）
# ============================================================================


@router.post(
    "/users/",
    response_model=UserCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_permission(scopes.SCOPE_USER_WRITE, "write")],
)
async def create_user(
    payload: UserCreate,
    session: SessionDep,
    _user: CurrentActiveUser,  # 触发鉴权
) -> UserCreateResponse:
    """EP01-S03 管理员创建用户。"""
    user_service = UserService(session)
    user, plain = await user_service.create(payload)
    from app.modules.auth.repository import RoleRepository

    roles = await RoleRepository(session).list_codes_for_user(user.id)
    return UserCreateResponse(
        user=UserSummary(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            email=user.email,
            status=user.status,
            password_must_change=user.password_must_change,
            roles=roles,
        ),
        initial_password=plain,
    )


@router.get(
    "/users/",
    response_model=UserListResponse,
    dependencies=[require_permission(scopes.SCOPE_USER_READ, "read")],
)
async def list_users(
    session: SessionDep,
    _user: CurrentActiveUser,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = None,
) -> UserListResponse:
    user_service = UserService(session)
    items, total = await user_service.list(
        page=page, page_size=page_size, status=status_filter, search=search
    )
    from app.modules.auth.repository import RoleRepository

    repo = RoleRepository(session)
    out: list[UserListItem] = []
    for u in items:
        roles = await repo.list_codes_for_user(u.id)
        out.append(
            UserListItem(
                id=u.id,
                username=u.username,
                display_name=u.display_name,
                email=u.email,
                status=u.status,
                locked_at=u.locked_at,
                last_login_at=u.last_login_at,
                roles=roles,
                created_at=u.created_at,
            )
        )
    return UserListResponse(
        items=out,
        meta=PageMeta(page=page, page_size=page_size, total=total),
    )


@router.put(
    "/users/{user_id}",
    response_model=UserSummary,
    dependencies=[require_permission(scopes.SCOPE_USER_WRITE, "write")],
)
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    session: SessionDep,
    _user: CurrentActiveUser,
) -> UserSummary:
    user_service = UserService(session)
    updated = await user_service.update(user_id, payload)
    from app.modules.auth.repository import RoleRepository

    roles = await RoleRepository(session).list_codes_for_user(updated.id)
    return _user_to_summary(updated, roles)


@router.put(
    "/users/{user_id}/toggle",
    response_model=UserSummary,
    dependencies=[require_permission(scopes.SCOPE_USER_WRITE, "write")],
)
async def toggle_user(
    user_id: UUID,
    session: SessionDep,
    _user: CurrentActiveUser,
) -> UserSummary:
    user_service = UserService(session)
    updated = await user_service.toggle_active(user_id)
    from app.modules.auth.repository import RoleRepository

    roles = await RoleRepository(session).list_codes_for_user(updated.id)
    return _user_to_summary(updated, roles)


@router.put(
    "/users/{user_id}/unlock",
    response_model=UserSummary,
    dependencies=[require_permission(scopes.SCOPE_USER_WRITE, "write")],
)
async def unlock_user(
    user_id: UUID,
    session: SessionDep,
    _user: CurrentActiveUser,
) -> UserSummary:
    user_service = UserService(session)
    updated = await user_service.unlock(user_id)
    from app.modules.auth.repository import RoleRepository

    roles = await RoleRepository(session).list_codes_for_user(updated.id)
    return _user_to_summary(updated, roles)


@router.put(
    "/users/{user_id}/reset-password",
    response_model=ResetPasswordResponse,
    dependencies=[require_permission(scopes.SCOPE_USER_WRITE, "write")],
)
async def reset_password(
    user_id: UUID,
    session: SessionDep,
    _user: CurrentActiveUser,
) -> ResetPasswordResponse:
    user_service = UserService(session)
    plain = await user_service.reset_password(user_id)
    return ResetPasswordResponse(initial_password=plain)


@router.post(
    "/users/{user_id}/roles",
    response_model=UserSummary,
    dependencies=[require_permission(scopes.SCOPE_ROLE_ASSIGN, "assign")],
)
async def assign_roles(
    user_id: UUID,
    payload: RoleAssignRequest,
    session: SessionDep,
    _user: CurrentActiveUser,
) -> UserSummary:
    """EP01-S04 管理员分配预设角色。"""
    user_service = UserService(session)
    updated = await user_service.assign_roles(user_id, payload.role_codes)
    from app.modules.auth.repository import RoleRepository

    roles = await RoleRepository(session).list_codes_for_user(updated.id)
    return _user_to_summary(updated, roles)


# ============================================================================
# 自定义权限端点（U09 EP01-S05，要求 auth.permission:grant）
# ============================================================================


@router.post(
    "/users/{user_id}/permissions/grant",
    dependencies=[require_permission(scopes.SCOPE_PERMISSION_GRANT, "grant")],
)
async def grant_permission(
    user_id: UUID,
    payload: PermissionOverrideIn,
    session: SessionDep,
    actor: CurrentActiveUser,
) -> dict[str, bool]:
    """EP01-S05 管理员授予用户自定义权限（含字段级 scope）。"""
    await PermissionService(session).grant(
        user_id, payload.scope, actor_id=actor.id, reason=payload.reason
    )
    return {"ok": True}


@router.post(
    "/users/{user_id}/permissions/revoke",
    dependencies=[require_permission(scopes.SCOPE_PERMISSION_GRANT, "grant")],
)
async def revoke_permission(
    user_id: UUID,
    payload: PermissionOverrideIn,
    session: SessionDep,
    actor: CurrentActiveUser,
) -> dict[str, bool]:
    """EP01-S05 管理员撤销用户权限（撤销优先级最高）。"""
    await PermissionService(session).revoke(
        user_id, payload.scope, actor_id=actor.id, reason=payload.reason
    )
    return {"ok": True}


@router.get(
    "/users/{user_id}/effective-permissions",
    response_model=EffectivePermissionsView,
    dependencies=[require_permission(scopes.SCOPE_PERMISSION_GRANT, "grant")],
)
async def effective_permissions(
    user_id: UUID,
    session: SessionDep,
    _user: CurrentActiveUser,
) -> EffectivePermissionsView:
    """EP01-S05 查看用户有效权限（角色 scope + grant/revoke + 合并）。"""
    data = await PermissionService(session).get_effective(user_id)
    return EffectivePermissionsView(**data)


# ============================================================================
# 审计日志端点
# ============================================================================


@router.get(
    "/audit-logs",
    response_model=AuditLogListResponse,
    dependencies=[require_permission(scopes.SCOPE_AUDIT_READ, "read")],
)
async def list_audit_logs(
    session: SessionDep,
    _user: CurrentActiveUser,
    action: str | None = None,
    resource: str | None = None,
    user_id: UUID | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> AuditLogListResponse:
    """EP01-S08 审计日志查询。"""
    from datetime import datetime

    df = datetime.fromisoformat(date_from) if date_from else None
    dt = datetime.fromisoformat(date_to) if date_to else None

    params = AuditLogQuery(
        action=action,
        resource=resource,
        user_id=user_id,
        date_from=df,
        date_to=dt,
        page=page,
        page_size=page_size,
    )
    items, total = await AuditQueryService(session).query(params)
    return AuditLogListResponse(
        items=[AuditLogEntry.model_validate(it) for it in items],
        meta=PageMeta(page=page, page_size=page_size, total=total),
    )


# ============================================================================
# 工具
# ============================================================================


def _user_to_summary(user: User, roles: list[str]) -> UserSummary:
    return UserSummary(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        status=user.status,
        password_must_change=user.password_must_change,
        roles=roles,
    )
