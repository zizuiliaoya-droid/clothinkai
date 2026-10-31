"""真实 PostgreSQL：平台身份不能从客户管理权限升级，开户与启停。"""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.core.db import get_bypass_session, get_session
from app.core.exceptions import PermissionDeniedError, TokenInvalidError, ValidationError
from app.core.security.auth import hash_password, verify_password
from app.core.tenancy import bypass_rls_ctx, tenant_id_ctx
from app.modules.auth.deps import get_current_user, get_current_user_active
from app.modules.auth.grant_platform_admin import grant
from app.modules.auth.models import AuditLog, Permission, Role, Tenant, User, UserPermissionOverride
from app.modules.auth.platform_access import is_platform_admin
from app.modules.auth.repository import RoleRepository
from app.modules.auth.schemas import LoginRequest, UserCreate, UserUpdate
from app.modules.auth.service import AuthService, PermissionService, UserService
from app.modules.auth.tenant_management import TenantManagementService
from tests.integration.test_auth_login import PASSWORD
from tests.integration.test_auth_login import stub_cache as login_cache_fixture

stub_cache = login_cache_fixture

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@contextmanager
def context(actor=None):
    bypass = bypass_rls_ctx.set(True)
    tenant = tenant_id_ctx.set(actor.tenant_id if actor is not None else None)
    try:
        yield
    finally:
        tenant_id_ctx.reset(tenant)
        bypass_rls_ctx.reset(bypass)


async def users(session, factory, tenant_a, admin_role):
    with context():
        default = (
            await session.execute(select(Tenant).where(Tenant.code == "default"))
        ).scalar_one()
        platform_role = (
            await session.execute(select(Role).where(Role.code == "platform_admin"))
        ).scalar_one()
        platform = await factory.user(default, roles=[admin_role, platform_role])
        ordinary = await factory.user(default, roles=[admin_role])
        customer = await factory.user(tenant_a, roles=[admin_role])
    return SimpleNamespace(
        platform=platform, ordinary=ordinary, customer=customer, platform_role=platform_role
    )


async def request(session, actor, method, path, payload=None):
    from app.main import app

    async def shared():
        with context(actor):
            yield session

    app.dependency_overrides[get_session] = shared
    app.dependency_overrides[get_bypass_session] = shared
    app.dependency_overrides[get_current_user_active] = lambda: actor
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, json=payload)
    finally:
        for dependency in (get_session, get_bypass_session, get_current_user_active):
            app.dependency_overrides.pop(dependency, None)


@pytest.mark.parametrize("kind", ["ordinary", "customer"])
async def test_admin_and_personal_star_cannot_list_create_or_disable_tenants(
    session, factory, tenant_a, admin_role, kind
):
    accounts = await users(session, factory, tenant_a, admin_role)
    actor = getattr(accounts, kind)
    with context(actor):
        star = (
            await session.execute(select(Permission).where(Permission.scope == "*"))
        ).scalar_one()
        session.add(
            UserPermissionOverride(
                tenant_id=actor.tenant_id, user_id=actor.id, permission_id=star.id, effect="grant"
            )
        )
        await session.flush()
    for method, path, payload in [
        ("GET", "/api/tenants", None),
        (
            "POST",
            "/api/tenants",
            {
                "code": "forbidden_" + uuid4().hex[:8],
                "name": "Forbidden",
                "admin_username": "admin",
            },
        ),
        ("PUT", f"/api/tenants/{tenant_a.id}/status", {"status": "disabled"}),
    ]:
        response = await request(session, actor, method, path, payload)
        assert response.status_code == 403, response.text
    await session.refresh(tenant_a)
    assert tenant_a.status == "active"


async def test_platform_role_outside_default_is_not_platform_identity(
    session, factory, tenant_a, admin_role
):
    accounts = await users(session, factory, tenant_a, admin_role)
    with context(accounts.customer):
        outsider = await factory.user(tenant_a, roles=[accounts.platform_role])
        assert not await is_platform_admin(session, outsider.id)
        with pytest.raises(PermissionDeniedError):
            await TenantManagementService(session).list(outsider, page=1, page_size=20)


async def test_platform_identity_still_respects_personal_deny_all(
    session, factory, tenant_a, admin_role
):
    accounts = await users(session, factory, tenant_a, admin_role)
    actor = accounts.platform
    with context(actor):
        star = (
            await session.execute(select(Permission).where(Permission.scope == "*"))
        ).scalar_one()
        session.add(
            UserPermissionOverride(
                tenant_id=actor.tenant_id, user_id=actor.id, permission_id=star.id, effect="revoke"
            )
        )
        await session.flush()
        assert await is_platform_admin(session, actor.id)
    for method, path, payload in [
        ("GET", "/api/tenants", None),
        (
            "POST",
            "/api/tenants",
            {"code": "denied_" + uuid4().hex[:8], "name": "Denied", "admin_username": "admin"},
        ),
        ("PUT", f"/api/tenants/{tenant_a.id}/status", {"status": "disabled"}),
    ]:
        response = await request(session, actor, method, path, payload)
        assert response.status_code == 403, response.text


async def test_platform_creates_once_with_no_store_password_and_actor_audit(
    session, factory, tenant_a, admin_role
):
    accounts = await users(session, factory, tenant_a, admin_role)
    payload = {"code": "web_" + uuid4().hex[:8], "name": "新客户", "admin_username": "admin"}
    response = await request(session, accounts.platform, "POST", "/api/tenants", payload)
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    created = response.json()
    with context(accounts.platform):
        tenant = (
            await session.execute(select(Tenant).where(Tenant.code == payload["code"]))
        ).scalar_one()
        account = (
            await session.execute(select(User).where(User.tenant_id == tenant.id))
        ).scalar_one()
        password_hash = account.password_hash
        assert verify_password(created["initial_password"], password_hash)
        assert account.password_must_change
        assert await RoleRepository(session).list_codes_for_user(account.id) == ["admin"]
        audit = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.resource_id == str(tenant.id), AuditLog.action == "tenant_create"
                )
            )
        ).scalar_one()
        assert (
            audit.user_id == accounts.platform.id and audit.tenant_id == accounts.platform.tenant_id
        )
        assert created["initial_password"] not in str(audit.after)
    repeated = await request(session, accounts.platform, "POST", "/api/tenants", payload)
    assert repeated.status_code in (409, 422)
    with context():
        assert (
            await session.execute(
                select(func.count()).select_from(Tenant).where(Tenant.code == payload["code"])
            )
        ).scalar_one() == 1
        await session.refresh(account)
        await session.refresh(accounts.platform)
        assert account.password_hash == password_hash
    listing = await request(session, accounts.platform, "GET", "/api/tenants")
    assert listing.status_code == 200
    assert "initial_password" not in listing.text


async def test_disable_invalidates_target_sessions_and_default_cannot_be_disabled(
    session, factory, tenant_a, admin_role, stub_cache
):
    accounts = await users(session, factory, tenant_a, admin_role)
    with context(), patch("app.modules.auth.service.cache", stub_cache):
        accounts.customer.password_hash = hash_password(PASSWORD)
        await session.flush()
        login = LoginRequest(
            tenant_code=tenant_a.code, username=accounts.customer.username, password=PASSWORD
        )
        access, refresh, _, _ = await AuthService(session).login(login, ip="127.0.0.1")
    with context(accounts.platform):
        customer_stamp = accounts.customer.password_changed_at
        other_stamp = accounts.ordinary.password_changed_at
        service = TenantManagementService(session)
        disabled = await service.set_status(accounts.platform, tenant_a.id, "disabled")
        assert disabled.status == "disabled"
        await session.refresh(accounts.customer)
        await session.refresh(accounts.ordinary)
        assert accounts.customer.password_changed_at != customer_stamp
        assert accounts.ordinary.password_changed_at == other_stamp
        await service.set_status(accounts.platform, tenant_a.id, "active")
        assert accounts.customer.password_changed_at != customer_stamp
        with pytest.raises(ValidationError):
            await service.set_status(accounts.platform, accounts.platform.tenant_id, "disabled")
    # 再次启用后，停用前的 access/refresh 也不能复活，但新登录仍可用。
    with context():
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
        with patch("app.modules.auth.service.cache", stub_cache):
            _, _, logged, _ = await AuthService(session).login(login, ip="127.0.0.1")
            assert logged.id == accounts.customer.id


@pytest.mark.parametrize(
    "operation",
    [
        "update",
        "toggle",
        "unlock",
        "reset_password",
        "roles",
        "grant",
        "revoke",
        "reset",
        "get_effective",
    ],
)
async def test_ordinary_default_admin_cannot_take_over_platform_account(
    session, factory, tenant_a, admin_role, operation
):
    accounts = await users(session, factory, tenant_a, admin_role)
    actor, target = accounts.ordinary, accounts.platform
    with context(actor):
        service = UserService(session)
        with pytest.raises(PermissionDeniedError):
            if operation == "update":
                await service.update(
                    target.id, UserUpdate(display_name="takeover"), actor_id=actor.id
                )
            elif operation == "toggle":
                await service.toggle_active(target.id, actor_id=actor.id)
            elif operation == "unlock":
                await service.unlock(target.id, actor_id=actor.id)
            elif operation == "reset_password":
                await service.reset_password(target.id, actor_id=actor.id)
            elif operation == "roles":
                await service.assign_roles(
                    target.id, ["admin", "platform_admin", "finance"], actor_id=actor.id
                )
            elif operation == "get_effective":
                await PermissionService(session).get_effective(target.id, actor_id=actor.id)
            else:
                await getattr(PermissionService(session), operation)(
                    target.id, "*", actor_id=actor.id
                )


async def test_reserved_role_cannot_be_added_removed_but_platform_can_keep_it(
    session, factory, tenant_a, admin_role, monkeypatch
):
    accounts = await users(session, factory, tenant_a, admin_role)
    monkeypatch.setattr("app.modules.auth.service.invalidate_user_permissions_cache", AsyncMock())
    with context(accounts.platform):
        service = UserService(session)
        with pytest.raises(PermissionDeniedError):
            await service.create(
                UserCreate(
                    username="forbidden_" + uuid4().hex[:8], role_codes=["admin", "platform_admin"]
                )
            )
        with pytest.raises(PermissionDeniedError):
            await service.assign_roles(
                accounts.ordinary.id, ["platform_admin"], actor_id=accounts.platform.id
            )
        with pytest.raises(PermissionDeniedError):
            await service.assign_roles(
                accounts.platform.id, ["admin"], actor_id=accounts.platform.id
            )
        await service.assign_roles(
            accounts.platform.id,
            ["platform_admin", "admin", "finance"],
            actor_id=accounts.platform.id,
        )
        assert set(await RoleRepository(session).list_codes_for_user(accounts.platform.id)) == {
            "admin",
            "platform_admin",
            "finance",
        }


async def test_operator_bootstrap_appends_role_once_preserving_password_and_admin(
    session, factory, tenant_a, admin_role
):
    accounts = await users(session, factory, tenant_a, admin_role)
    actor = accounts.ordinary
    old_hash, old_stamp = actor.password_hash, actor.password_changed_at
    with context():
        user, changed = await grant(session, actor.username)
        assert changed and user.password_hash == old_hash and user.password_changed_at != old_stamp
        assert set(await RoleRepository(session).list_codes_for_user(user.id)) == {
            "admin",
            "platform_admin",
        }
        again, changed_again = await grant(session, actor.username)
        assert not changed_again and again.id == user.id
