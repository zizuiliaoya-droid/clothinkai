"""auth 模块 Pydantic Schema。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.config import settings
from app.modules.auth.exceptions import WeakPasswordError

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-.]{3,64}$")


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int


# ---------------------------------------------------------------------------
# 认证
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_change_password: bool = False
    expires_in: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    # 长度由 _check_strength 校验（统一抛 WeakPasswordError，BR-PWD-001）；
    # 此处不设 min_length 以免 Pydantic 内建约束抢先抛 ValidationError 而非 WeakPasswordError。
    new_password: str = Field(..., max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_strength(cls, v: str) -> str:
        """BR-PWD-001: ≥10 字符 + 大写 + 小写 + 数字。"""
        if len(v) < 10:
            raise WeakPasswordError("密码长度需 ≥10 字符")
        if not re.search(r"[A-Z]", v):
            raise WeakPasswordError("密码需含至少 1 个大写字母")
        if not re.search(r"[a-z]", v):
            raise WeakPasswordError("密码需含至少 1 个小写字母")
        if not re.search(r"\d", v):
            raise WeakPasswordError("密码需含至少 1 个数字")
        return v


# ---------------------------------------------------------------------------
# 当前用户
# ---------------------------------------------------------------------------


class UserSummary(BaseModel):
    """登录后返回的精简用户信息。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str | None = None
    email: str | None = None
    status: str
    must_change_password: bool = Field(default=False, alias="password_must_change")
    roles: list[str] = []


# ---------------------------------------------------------------------------
# 用户管理
# ---------------------------------------------------------------------------


class UserCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str
    display_name: str | None = None
    email: EmailStr | None = None
    role_codes: list[str] = Field(default_factory=list)

    @field_validator("username")
    @classmethod
    def _validate_username(cls, v: str) -> str:
        if not USERNAME_PATTERN.match(v):
            raise ValueError("用户名只允许 a-z A-Z 0-9 _ - . 且长度 3-64")
        return v


class UserUpdate(BaseModel):
    display_name: str | None = None
    email: EmailStr | None = None


class UserCreateResponse(BaseModel):
    """创建用户响应：含一次性返回的临时密码（不写日志）。"""

    user: UserSummary
    initial_password: str = Field(
        ...,
        description="临时密码，仅本响应一次性返回；后续无法再获取",
    )


class UserListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    display_name: str | None = None
    email: str | None = None
    status: str
    locked_at: datetime | None = None
    last_login_at: datetime | None = None
    roles: list[str] = []
    created_at: datetime


class UserListResponse(BaseModel):
    items: list[UserListItem]
    meta: PageMeta


class RoleAssignRequest(BaseModel):
    role_codes: list[str]


class ResetPasswordResponse(BaseModel):
    initial_password: str = Field(..., description="重置后的临时密码（一次性返回）")


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------


class AuditLogQuery(BaseModel):
    action: str | None = None
    resource: str | None = None
    user_id: UUID | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


class AuditLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: UUID | None = None
    user_id: UUID | None = None
    actor_type: str
    action: str
    resource: str | None = None
    resource_id: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    purpose: str | None = None
    ip: str | None = None
    request_id: str | None = None
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogEntry]
    meta: PageMeta


# ---------------------------------------------------------------------------
# 自定义权限（U09 EP01-S05）
# ---------------------------------------------------------------------------


class PermissionOverrideIn(BaseModel):
    """grant / revoke 请求体。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    scope: str = Field(..., min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=512)


class EffectivePermissionsView(BaseModel):
    """有效权限视图（角色 scope + 自定义 grant/revoke + 合并结果）。"""

    user_id: str
    role_scopes: list[str]
    grants: list[str]
    revokes: list[str]
    effective: list[str]
