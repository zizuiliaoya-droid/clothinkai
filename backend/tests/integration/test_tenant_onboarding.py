"""真实 PostgreSQL：同名账号登录、审计隔离、停用租户与开户原子性。"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select

from app.core.audit import AuditService
from app.core.exceptions import (
    InvalidCredentialsError,
    TenantContextMissingError,
    TokenInvalidError,
)
from app.core.security.auth import decode_token, hash_password, verify_password
from app.core.tenancy import tenant_id_ctx
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import Tenant, User, UserRole
from app.modules.auth.repository import AuditLogRepository
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import AuthService
from app.modules.auth.tenant_onboarding import TenantOnboarding, provision_tenant
from tests.integration.test_auth_login import PASSWORD, _login_ctx
from tests.integration.test_auth_login import stub_cache as login_cache_fixture

stub_cache = login_cache_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_same_username_login_is_scoped(session, tenant_a, tenant_b, factory, stub_cache):
    with _login_ctx():
        a = await factory.user(
            tenant_a, username="same_admin", password_hash=hash_password(PASSWORD)
        )
        b = await factory.user(
            tenant_b, username="same_admin", password_hash=hash_password("OtherPwd123")
        )
        with patch("app.modules.auth.service.cache", stub_cache):
            for tenant, password, user in [(tenant_a, PASSWORD, a), (tenant_b, "OtherPwd123", b)]:
                access, _, logged, _ = await AuthService(session).login(
                    LoginRequest(tenant_code=tenant.code, username="same_admin", password=password),
                    ip="127.0.0.1",
                )
                assert logged.id == user.id
                assert decode_token(access, expected_type="access")["tenant_id"] == str(tenant.id)
            with pytest.raises(InvalidCredentialsError):
                await AuthService(session).login(
                    LoginRequest(
                        tenant_code=tenant_b.code, username="same_admin", password=PASSWORD
                    ),
                    ip="127.0.0.1",
                )


async def test_missing_tenant_code_uses_default_only(session, tenant_b, factory, stub_cache):
    with _login_ctx():
        default = (
            await session.execute(select(Tenant).where(Tenant.code == "default"))
        ).scalar_one()
        username = f"compat_{uuid4().hex[:8]}"
        a = await factory.user(default, username=username, password_hash=hash_password(PASSWORD))
        await factory.user(tenant_b, username=username, password_hash=hash_password("OtherPwd123"))
        with patch("app.modules.auth.service.cache", stub_cache):
            for code in [None, "   "]:
                fields = {} if code is None else {"tenant_code": code}
                _, _, user, _ = await AuthService(session).login(
                    LoginRequest(username=username, password=PASSWORD, **fields), ip="127.0.0.1"
                )
                assert user.id == a.id


async def test_rate_limit_is_not_shared_between_tenants(
    session, tenant_a, tenant_b, factory, stub_cache
):
    with _login_ctx():
        for tenant in [tenant_a, tenant_b]:
            await factory.user(tenant, username="same_admin", password_hash=hash_password(PASSWORD))
        with patch("app.modules.auth.service.cache", stub_cache):
            for _ in range(5):
                with pytest.raises(InvalidCredentialsError):
                    await AuthService(session).login(
                        LoginRequest(
                            tenant_code=tenant_a.code, username="same_admin", password="wrong"
                        ),
                        ip="127.0.0.1",
                    )
            _, _, user, _ = await AuthService(session).login(
                LoginRequest(tenant_code=tenant_b.code, username="same_admin", password=PASSWORD),
                ip="127.0.0.1",
            )
            assert user.tenant_id == tenant_b.id


@pytest.mark.parametrize("state", ["missing", "disabled", "deleted"])
async def test_unavailable_tenant_has_generic_login_error(
    session, tenant_a, factory, stub_cache, state
):
    with _login_ctx():
        await factory.user(tenant_a, username="tenant_admin", password_hash=hash_password(PASSWORD))
        if state == "disabled":
            tenant_a.status = "disabled"
        if state == "deleted":
            tenant_a.deleted_at = datetime.now(UTC)
        await session.flush()
        code = "nonexistent_tenant" if state == "missing" else tenant_a.code
        with (
            patch("app.modules.auth.service.cache", stub_cache),
            pytest.raises(InvalidCredentialsError),
        ):
            await AuthService(session).login(
                LoginRequest(tenant_code=code, username="tenant_admin", password=PASSWORD),
                ip="127.0.0.1",
            )


async def test_disabling_tenant_rejects_existing_access_and_refresh(
    session, tenant_a, factory, stub_cache
):
    with _login_ctx():
        await factory.user(tenant_a, username="tenant_admin", password_hash=hash_password(PASSWORD))
        with patch("app.modules.auth.service.cache", stub_cache):
            access, refresh, _, _ = await AuthService(session).login(
                LoginRequest(tenant_code=tenant_a.code, username="tenant_admin", password=PASSWORD),
                ip="127.0.0.1",
            )
        tenant_a.status = "disabled"
        await session.flush()
        with (
            patch("app.modules.auth.deps.is_revoked", return_value=False),
            pytest.raises(TokenInvalidError),
        ):
            await get_current_user(
                HTTPAuthorizationCredentials(scheme="Bearer", credentials=access), session
            )
        with (
            patch("app.modules.auth.service.is_revoked", return_value=False),
            pytest.raises(TokenInvalidError),
        ):
            await AuthService(session).refresh(refresh)


async def test_audit_list_total_and_foreign_user_filter_are_scoped(session, tenant_a, tenant_b):
    other_user_id = uuid4()
    for tenant, count in [(tenant_a, 2), (tenant_b, 3)]:
        for _ in range(count):
            await AuditService(session).log(
                action="tenant_probe",
                tenant_id=tenant.id,
                user_id=other_user_id if tenant == tenant_b else None,
            )
    await session.flush()
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        rows, total = await AuditLogRepository(session).query(action="tenant_probe", page_size=1)
        assert len(rows) == 1 and total == 2
        assert rows[0].tenant_id == tenant_a.id
        rows, total = await AuditLogRepository(session).query(
            action="tenant_probe", user_id=other_user_id
        )
        assert rows == [] and total == 0
    finally:
        tenant_id_ctx.reset(token)
    with pytest.raises(TenantContextMissingError):
        await AuditLogRepository(session).query()


async def test_provision_tenant_and_duplicate_do_not_overwrite(session, admin_role):
    request = TenantOnboarding(code=f"trial_{uuid4().hex[:8]}", name="测试客户")
    with _login_ctx():
        async with session.begin_nested():
            result = await provision_tenant(session, request)
        user = (
            await session.execute(select(User).where(User.tenant_id == result.tenant_id))
        ).scalar_one()
        assert user.password_must_change and verify_password(
            result.initial_password, user.password_hash
        )
        roles = (
            (await session.execute(select(UserRole.role_id).where(UserRole.user_id == user.id)))
            .scalars()
            .all()
        )
        assert roles == [admin_role.id]
        old_hash = user.password_hash
        with pytest.raises(ValueError):
            async with session.begin_nested():
                await provision_tenant(session, request)
        await session.refresh(user)
        assert user.password_hash == old_hash


async def test_provision_rolls_back_partially_created_tenant(session, admin_role):
    request = TenantOnboarding(code=f"rollback_{uuid4().hex[:8]}", name="回滚测试")
    with _login_ctx():
        with (
            pytest.raises(RuntimeError),
            patch(
                "app.modules.auth.tenant_onboarding.hash_password",
                side_effect=RuntimeError("synthetic failure"),
            ),
        ):
            async with session.begin_nested():
                await provision_tenant(session, request)
        count = (
            await session.execute(
                select(func.count()).select_from(Tenant).where(Tenant.code == request.code)
            )
        ).scalar_one()
        assert count == 0
