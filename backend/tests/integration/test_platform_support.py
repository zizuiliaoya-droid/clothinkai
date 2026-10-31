"""真实 PG：平台正常会话隔离、只读目标绑定/失效与事务只读。"""

from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_bypass_session, get_session
from app.core.exceptions import PermissionDeniedError, TokenInvalidError
from app.core.security.auth import decode_token, encode_access_token
from app.core.tenancy import bypass_rls_ctx, support_readonly_ctx
from app.modules.auth import support_access
from app.modules.auth.models import AuditLog, Permission, UserPermissionOverride, UserRole
from app.modules.auth.service import AuthService
from tests.integration.test_tenant_management import context, users

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def access(user):
    return encode_access_token(
        user_id=user.id, tenant_id=user.tenant_id, roles=[], pwd_iat=user.password_changed_at
    )[0]


@asynccontextmanager
async def client(session, monkeypatch):
    from app.main import app

    async def shared():
        yield session

    async def bypass():
        token = bypass_rls_ctx.set(True)
        try:
            yield session
        finally:
            bypass_rls_ctx.reset(token)

    # 测试数据在外层 rollback 事务内；鉴权审计只 flush 保留同连接可见性。
    # 真实 READ ONLY 的跨 commit 行为由下面独立连接用例验证。
    class AuditSession:
        def __getattr__(self, name):
            return getattr(session, name)

        async def commit(self):
            await session.flush()

    @asynccontextmanager
    async def auth_session():
        readonly = support_readonly_ctx.set(False)
        try:
            with context():
                yield AuditSession()
        finally:
            support_readonly_ctx.reset(readonly)

    monkeypatch.setattr(support_access, "support_auth_session", auth_session)
    monkeypatch.setattr(support_access, "is_revoked", AsyncMock(return_value=False))
    monkeypatch.setattr("app.modules.auth.deps.is_revoked", AsyncMock(return_value=False))
    app.dependency_overrides[get_session] = shared
    app.dependency_overrides[get_bypass_session] = bypass
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            yield http
    finally:
        app.dependency_overrides.pop(get_session, None)
        app.dependency_overrides.pop(get_bypass_session, None)


async def test_platform_normal_token_is_console_only_and_customer_unchanged(
    session, factory, tenant_a, admin_role, monkeypatch
):
    accounts = await users(session, factory, tenant_a, admin_role)
    async with client(session, monkeypatch) as http:
        headers = {"Authorization": "Bearer " + access(accounts.platform)}
        me = await http.get("/api/auth/me", headers=headers)
        assert me.status_code == 200 and me.json()["session_mode"] == "platform"
        for method, path, payload in [
            ("GET", "/api/bloggers/", None),
            ("GET", "/api/users/", None),
            ("POST", "/api/users/", {"username": "cannot_create", "role_codes": []}),
            (
                "POST",
                "/api/attachments/upload-init",
                {
                    "bucket": "private",
                    "purpose": "payment_proof",
                    "mime_type": "image/png",
                    "size_bytes": 1,
                },
            ),
        ]:
            response = await http.request(method, path, json=payload, headers=headers)
            assert response.status_code == 403, response.text
            assert response.json()["code"] == "PLATFORM_CONSOLE_ONLY"
        tenants = await http.get("/api/tenants", headers=headers)
        assert tenants.status_code == 200
        ordinary = {"Authorization": "Bearer " + access(accounts.ordinary)}
        assert (await http.get("/api/users/", headers=ordinary)).status_code == 200
        denied = await http.post(
            "/api/platform/support-sessions", headers=ordinary, json={"tenant_id": str(tenant_a.id)}
        )
        assert denied.status_code == 403


async def test_support_only_reads_selected_customer_and_audits_real_actor(
    session, factory, tenant_a, admin_role, monkeypatch
):
    accounts = await users(session, factory, tenant_a, admin_role)
    with context(accounts.platform):
        issued = await support_access.issue_support_token(session, accounts.platform, tenant_a.id)
    token = issued["access_token"]
    claims = decode_token(token, expected_type="support")
    assert claims["sub"] == str(accounts.customer.id)
    async with client(session, monkeypatch) as http:
        headers = {"Authorization": "Bearer " + token}
        me = await http.get("/api/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        assert me.json()["session_mode"] == "support"
        assert me.json()["support_context"]["actor"]["id"] == str(accounts.platform.id)
        assert me.json()["support_context"]["tenant"]["id"] == str(tenant_a.id)
        listing = await http.get("/api/users/", headers=headers)
        assert listing.status_code == 200, listing.text
        assert {row["id"] for row in listing.json()["items"]} == {str(accounts.customer.id)}
        outside = await http.get(
            f"/api/users/{accounts.ordinary.id}/effective-permissions", headers=headers
        )
        assert outside.status_code == 404
        detail = await http.get(
            f"/api/users/{accounts.customer.id}/effective-permissions", headers=headers
        )
        assert detail.status_code == 200, detail.text
        for method, path, payload in [
            (
                "PUT",
                "/api/auth/password",
                {"old_password": "unused", "new_password": "StrongNew123!"},
            ),
            ("POST", "/api/auth/logout", None),
            ("POST", "/api/auth/refresh", {"refresh_token": token}),
            (
                "POST",
                "/api/platform/support-sessions",
                {"tenant_id": str(accounts.ordinary.tenant_id)},
            ),
            ("PUT", f"/api/tenants/{tenant_a.id}/status", {"status": "disabled"}),
        ]:
            response = await http.request(method, path, json=payload, headers=headers)
            assert response.status_code == 403 and response.json()["code"] == "SUPPORT_READ_ONLY"
    with context():
        audit = (
            (
                await session.execute(
                    select(AuditLog).where(
                        AuditLog.action == "support_view", AuditLog.user_id == accounts.platform.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert audit and all(row.tenant_id == tenant_a.id for row in audit)
        assert all(row.after["customer_user_id"] == str(accounts.customer.id) for row in audit)
        with pytest.raises(TokenInvalidError):
            await AuthService(session).refresh(token)


@pytest.mark.parametrize(
    "change",
    [
        "owner_stamp",
        "owner_role",
        "owner_deny",
        "tenant_disabled",
        "customer_stamp",
        "customer_disabled",
        "customer_role",
        "different_tenant",
    ],
)
async def test_support_rechecks_live_identity_and_target(
    session, factory, tenant_a, admin_role, change
):
    accounts = await users(session, factory, tenant_a, admin_role)
    with context(accounts.platform):
        issued = await support_access.issue_support_token(session, accounts.platform, tenant_a.id)
        claims = decode_token(issued["access_token"], expected_type="support")
        if change == "owner_stamp":
            accounts.platform.password_changed_at += timedelta(seconds=1)
        elif change == "owner_role":
            await session.execute(
                delete(UserRole).where(
                    UserRole.user_id == accounts.platform.id,
                    UserRole.role_id == accounts.platform_role.id,
                )
            )
        elif change == "owner_deny":
            star = (
                await session.execute(select(Permission).where(Permission.scope == "*"))
            ).scalar_one()
            session.add(
                UserPermissionOverride(
                    tenant_id=accounts.platform.tenant_id,
                    user_id=accounts.platform.id,
                    permission_id=star.id,
                    effect="revoke",
                )
            )
        elif change == "tenant_disabled":
            tenant_a.status = "disabled"
        elif change == "customer_stamp":
            accounts.customer.password_changed_at += timedelta(seconds=1)
        elif change == "customer_disabled":
            accounts.customer.status = "disabled"
        elif change == "customer_role":
            await session.execute(
                delete(UserRole).where(
                    UserRole.user_id == accounts.customer.id, UserRole.role_id == admin_role.id
                )
            )
        else:
            claims["tenant_id"] = str(accounts.ordinary.tenant_id)
        await session.flush()
        with pytest.raises((TokenInvalidError, PermissionDeniedError)):
            await support_access.validate_support_identity(session, claims)


async def test_support_readonly_applies_again_after_commit(engine):
    token = support_readonly_ctx.set(True)
    bypass = bypass_rls_ctx.set(False)
    try:
        async with AsyncSession(engine) as fresh:
            assert (await fresh.execute(text("SHOW transaction_read_only"))).scalar_one() == "on"
            await fresh.commit()
            assert (await fresh.execute(text("SHOW transaction_read_only"))).scalar_one() == "on"
            with pytest.raises(DBAPIError) as exc:
                await fresh.execute(text("DELETE FROM role WHERE id = :id"), {"id": UUID(int=0)})
            assert "read-only" in str(exc.value).lower()
            await fresh.rollback()
    finally:
        bypass_rls_ctx.reset(bypass)
        support_readonly_ctx.reset(token)


async def test_owner_business_read_denial_is_rechecked_after_support_issuance(
    session, factory, tenant_a, admin_role, monkeypatch
):
    accounts = await users(session, factory, tenant_a, admin_role)
    with context(accounts.platform):
        issued = await support_access.issue_support_token(session, accounts.platform, tenant_a.id)
        permission = (
            await session.execute(select(Permission).where(Permission.scope == "blogger:read"))
        ).scalar_one()
        session.add(
            UserPermissionOverride(
                tenant_id=accounts.platform.tenant_id,
                user_id=accounts.platform.id,
                permission_id=permission.id,
                effect="revoke",
            )
        )
        await session.flush()
    async with client(session, monkeypatch) as http:
        headers = {"Authorization": "Bearer " + issued["access_token"]}
        denied = await http.get("/api/bloggers/", headers=headers)
        assert denied.status_code == 403, denied.text
        allowed = await http.get("/api/users/", headers=headers)
        assert allowed.status_code == 200, allowed.text
