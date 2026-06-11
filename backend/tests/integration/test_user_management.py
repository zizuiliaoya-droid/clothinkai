"""集成测试：EP01-S03 用户管理 + EP01-S04 角色分配。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ResourceNotFoundError
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.exceptions import (
    CannotUnlockUserError,
    RoleNotFoundError,
    UsernameAlreadyExistsError,
)
from app.modules.auth.models import Role, Tenant, User
from app.modules.auth.schemas import UserCreate
from app.modules.auth.service import UserService


@pytest.fixture
def stub_cache() -> AsyncMock:
    mock = AsyncMock()
    mock.delete = AsyncMock(return_value=0)
    mock.setex = AsyncMock()
    return mock


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateUser:
    async def test_create_with_initial_password(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        admin_role: Role,
        stub_cache: AsyncMock,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                svc = UserService(session)
                user, plain = await svc.create(
                    UserCreate(
                        username="alice",
                        display_name="Alice",
                        email="alice@example.com",
                        role_codes=["admin"],
                    )
                )
            assert user.username == "alice"
            assert user.password_must_change is True
            assert len(plain) == 16
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_duplicate_username(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            await factory.user(tenant_a, username="duplicate")  # type: ignore[attr-defined]
            with patch("app.modules.auth.service.cache", stub_cache):
                svc = UserService(session)
                with pytest.raises(UsernameAlreadyExistsError):
                    await svc.create(UserCreate(username="duplicate"))
        finally:
            tenant_id_ctx.reset(token)

    async def test_create_with_invalid_role(
        self, session: AsyncSession, tenant_a: Tenant, stub_cache: AsyncMock
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            with patch("app.modules.auth.service.cache", stub_cache):
                svc = UserService(session)
                with pytest.raises(RoleNotFoundError):
                    await svc.create(
                        UserCreate(username="bob", role_codes=["nonexistent_role"])
                    )
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestToggleAndUnlock:
    async def test_toggle_active_to_disabled(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, status="active")  # type: ignore[attr-defined]
            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                svc = UserService(session)
                updated = await svc.toggle_active(user.id)
            assert updated.status == "disabled"

            # 再次 toggle 回 active
            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                updated = await svc.toggle_active(user.id)
            assert updated.status == "active"
        finally:
            tenant_id_ctx.reset(token)

    async def test_unlock_locked_user(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        from datetime import datetime, timezone

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a)  # type: ignore[attr-defined]
            user.locked_at = datetime.now(timezone.utc)
            user.failed_login_count = 10
            await session.flush()

            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                svc = UserService(session)
                updated = await svc.unlock(user.id)
            assert updated.locked_at is None
            assert updated.failed_login_count == 0
        finally:
            tenant_id_ctx.reset(token)

    async def test_unlock_not_locked_user_raises(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a)  # type: ignore[attr-defined]
            with patch("app.modules.auth.service.cache", stub_cache):
                svc = UserService(session)
                with pytest.raises(CannotUnlockUserError):
                    await svc.unlock(user.id)
        finally:
            tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestRoleAssignment:
    async def test_assign_roles(
        self,
        session: AsyncSession,
        tenant_a: Tenant,
        admin_role: Role,
        designer_role: Role,
        factory: object,
        stub_cache: AsyncMock,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a)  # type: ignore[attr-defined]
            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                svc = UserService(session)
                # 分配 admin + designer
                await svc.assign_roles(user.id, ["admin", "designer"])

            from app.modules.auth.repository import RoleRepository

            codes = await RoleRepository(session).list_codes_for_user(user.id)
            assert set(codes) == {"admin", "designer"}

            # 改为只保留 designer
            with patch("app.modules.auth.service.cache", stub_cache), patch(
                "app.core.security.permissions.cache", stub_cache
            ):
                await svc.assign_roles(user.id, ["designer"])
            codes = await RoleRepository(session).list_codes_for_user(user.id)
            assert codes == ["designer"]
        finally:
            tenant_id_ctx.reset(token)
