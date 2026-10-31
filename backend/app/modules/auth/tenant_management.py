"""小规模内测的租户管理：明确平台身份、原子开户和启停。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.exceptions import (
    DuplicateResourceError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationError,
)
from app.modules.auth.models import RefreshToken, Tenant, User
from app.modules.auth.platform_access import require_platform_admin
from app.modules.auth.schemas import PageMeta
from app.modules.auth.service import AuthService
from app.modules.auth.tenant_onboarding import TenantOnboarding, provision_tenant


class TenantView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    code: str
    name: str
    status: str
    created_at: datetime


class TenantListResponse(BaseModel):
    items: list[TenantView]
    meta: PageMeta


class TenantCreatedResponse(BaseModel):
    tenant: TenantView
    admin_username: str
    initial_password: str
    must_change_password: bool = True


class TenantStatusRequest(BaseModel):
    status: Literal["active", "disabled"]


class TenantManagementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _authorize(self, actor: User, action: str) -> None:
        await require_platform_admin(self.session, actor.id)
        # 平台身份是额外门槛，仍遵守已有用户管理权限和个人拒绝优先。
        permissions = await AuthService(self.session).load_effective_permissions(actor.id)
        if not permissions.has("auth.user", action):
            raise PermissionDeniedError(f"缺少租户管理所需权限 auth.user:{action}")

    async def list(self, actor: User, *, page: int, page_size: int) -> TenantListResponse:
        await self._authorize(actor, "read")
        condition = Tenant.deleted_at.is_(None)
        total = (
            await self.session.execute(select(func.count()).select_from(Tenant).where(condition))
        ).scalar_one()
        rows = (
            (
                await self.session.execute(
                    select(Tenant)
                    .where(condition)
                    .order_by(Tenant.created_at.desc(), Tenant.id)
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )
        return TenantListResponse(
            items=[TenantView.model_validate(row) for row in rows],
            meta=PageMeta(page=page, page_size=page_size, total=total),
        )

    async def create(self, actor: User, payload: TenantOnboarding) -> TenantCreatedResponse:
        await self._authorize(actor, "write")
        try:
            result = await provision_tenant(
                self.session, payload, actor_id=actor.id, audit_tenant_id=actor.tenant_id
            )
            tenant = await self.session.get(Tenant, result.tenant_id)
            view = TenantView.model_validate(tenant)
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise DuplicateResourceError("租户代码已存在，未覆盖任何账号") from exc
        except ValueError as exc:
            await self.session.rollback()
            raise ValidationError(str(exc)) from exc
        # 只有事务成功提交后才向调用者交付密码。
        return TenantCreatedResponse(
            tenant=view, admin_username=result.username, initial_password=result.initial_password
        )

    async def set_status(self, actor: User, tenant_id: UUID, status: str) -> TenantView:
        await self._authorize(actor, "write")
        tenant = (
            await self.session.execute(
                select(Tenant)
                .where(Tenant.id == tenant_id, Tenant.deleted_at.is_(None))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if tenant is None:
            raise ResourceNotFoundError("租户不存在")
        if tenant.code == "default" and status != "active":
            raise ValidationError("平台管理租户不能停用")
        if tenant.status != status:
            before = tenant.status
            tenant.status = status
            if status == "disabled":
                now = datetime.now(UTC)
                # 停用后旧登录不在再次启用时复活；账号自身状态不改。
                await self.session.execute(
                    update(User).where(User.tenant_id == tenant.id).values(password_changed_at=now)
                )
                await self.session.execute(
                    update(RefreshToken)
                    .where(RefreshToken.tenant_id == tenant.id, RefreshToken.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
            await AuditService(self.session).log(
                action="tenant_status",
                actor_type="user",
                user_id=actor.id,
                tenant_id=actor.tenant_id,
                resource="tenant",
                resource_id=tenant.id,
                before={"status": before},
                after={"code": tenant.code, "status": status},
            )
        await self.session.commit()
        return TenantView.model_validate(tenant)
