"""运维开户用例：同一事务创建租户、管理员和开户审计，不提交调用者事务。"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.security.auth import hash_password
from app.core.tenancy import bypass_rls_ctx
from app.modules.auth.domain import generate_random_password
from app.modules.auth.models import Role, Tenant, User, UserRole
from app.modules.auth.schemas import USERNAME_PATTERN


class TenantOnboarding(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    code: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=128)
    admin_username: str = Field(default="admin", min_length=3, max_length=64)

    @field_validator("admin_username")
    @classmethod
    def valid_username(cls, value: str) -> str:
        if not USERNAME_PATTERN.fullmatch(value):
            raise ValueError("用户名只允许字母、数字、下划线、短横线和点")
        return value


@dataclass(frozen=True)
class TenantOnboardingResult:
    tenant_id: UUID
    username: str
    initial_password: str


async def provision_tenant(
    session: AsyncSession,
    request: TenantOnboarding,
    *,
    actor_id: UUID | None = None,
    audit_tenant_id: UUID | None = None,
) -> TenantOnboardingResult:
    """由运维或已验证的平台入口调用；调用者负责事务与一次性交付临时密码。"""
    if not bypass_rls_ctx.get():
        raise ValueError("开户需要运维会话")
    if (
        await session.execute(select(Tenant.id).where(Tenant.code == request.code))
    ).scalar_one_or_none():
        raise ValueError("租户代码已存在；未覆盖任何现有账号")
    role = (await session.execute(select(Role).where(Role.code == "admin"))).scalar_one_or_none()
    if role is None:
        raise ValueError("缺少管理员角色，请先完成数据库初始化")
    tenant = Tenant(code=request.code, name=request.name, status="active")
    session.add(tenant)
    await session.flush()
    password = generate_random_password(16)
    admin = User(
        tenant_id=tenant.id,
        username=request.admin_username,
        password_hash=hash_password(password),
        status="active",
        password_must_change=True,
    )
    session.add(admin)
    await session.flush()
    session.add(UserRole(tenant_id=tenant.id, user_id=admin.id, role_id=role.id))
    await AuditService(session).log(
        action="tenant_create",
        actor_type="user" if actor_id is not None else "system",
        user_id=actor_id,
        resource="tenant",
        resource_id=tenant.id,
        tenant_id=audit_tenant_id or tenant.id,
        after={"code": request.code, "admin_username": request.admin_username},
    )
    await session.flush()
    return TenantOnboardingResult(tenant.id, admin.username, password)
