"""只读排查的 HTTP 拒绝边界、上下文清理和签发查询编译。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.dialects import postgresql

from app.core.config import settings
from app.core.exceptions import TokenInvalidError
from app.core.middleware.tenancy import TenancyContextMiddleware
from app.core.security.auth import decode_token
from app.core.tenancy import bypass_rls_ctx, support_readonly_ctx
from app.modules.auth import support_access
from app.modules.auth.support_policy import SUPPORT_READ_PATHS

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def signed(payload=None, *, secret=None):
    data = {
        "typ": "support",
        "sub": str(uuid4()),
        "tenant_id": str(uuid4()),
        "exp": datetime.now(UTC) + timedelta(minutes=15),
    }
    data.update(payload or {})
    return jwt.encode(
        data, secret or settings.JWT_SECRET.get_secret_value(), algorithm=settings.JWT_ALGORITHM
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/promotions/"),
        ("PUT", "/api/auth/password"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/refresh"),
        ("POST", "/api/platform/support-sessions"),
        ("POST", "/api/tenants"),
        ("POST", "/api/attachments/upload-init"),
        ("GET", "/api/credentials"),
        ("GET", "/api/collect/worker-token"),
        ("GET", "/api/reports/production/export"),
        ("GET", "/api/promotions/export"),
        ("GET", "/api/new-unreviewed-get"),
    ],
)
async def test_support_rejects_before_any_endpoint_side_effect(method, path):
    app = FastAPI()
    app.add_middleware(TenancyContextMiddleware)
    invoked = Mock()

    async def endpoint():
        invoked()
        return {"ok": True}

    app.add_api_route(path, endpoint, methods=[method])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(
            method, path, headers={"Authorization": "Bearer " + signed()}
        )
    assert response.status_code == 403
    assert response.json()["code"] == "SUPPORT_READ_ONLY"
    invoked.assert_not_called()


@pytest.mark.parametrize("mode", ["expired", "forged"])
async def test_support_signature_and_expiry_checked_before_endpoint(mode):
    app = FastAPI()
    app.add_middleware(TenancyContextMiddleware)
    endpoint = Mock(return_value={"ok": True})
    app.add_api_route("/api/auth/me", endpoint, methods=["GET"])
    token = (
        signed({"exp": datetime.now(UTC) - timedelta(seconds=1)})
        if mode == "expired"
        else signed(secret="untrusted-secret")
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/auth/me", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 401
    endpoint.assert_not_called()


async def test_support_context_does_not_leak_and_actor_claim_cannot_enable_bypass():
    app = FastAPI()
    app.add_middleware(TenancyContextMiddleware)

    @app.get("/api/auth/me")
    async def context():
        return {"readonly": support_readonly_ctx.get(), "bypass": bypass_rls_ctx.get()}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        support = await client.get("/api/auth/me", headers={"Authorization": "Bearer " + signed()})
        normal = await client.get(
            "/api/auth/me",
            headers={
                "Authorization": "Bearer "
                + signed({"typ": "access", "actor_type": "platform_admin"})
            },
        )
        anonymous = await client.get("/api/auth/me")
    assert support.json() == {"readonly": True, "bypass": False}
    assert normal.json() == anonymous.json() == {"readonly": False, "bypass": False}


async def test_support_issuance_compiles_correlated_query_and_has_no_refresh(monkeypatch):
    now = datetime.now(UTC)
    actor = SimpleNamespace(id=uuid4(), password_changed_at=now)
    tenant = SimpleNamespace(id=uuid4(), code="trial", name="客户")
    customer = SimpleNamespace(id=uuid4(), password_changed_at=now)
    statements = []

    async def execute(statement):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return SimpleNamespace(
            scalar_one_or_none=lambda: tenant if len(statements) == 1 else customer
        )

    session = SimpleNamespace(execute=execute, add=Mock(), commit=AsyncMock())
    monkeypatch.setattr(support_access, "_authorize_actor", AsyncMock())
    result = await support_access.issue_support_token(session, actor, tenant.id)
    assert "NOT (EXISTS" in statements[1] and "FROM user_role JOIN role" in statements[1]
    assert "refresh_token" not in result and result["expires_in"] == 900
    claims = decode_token(result["access_token"], expected_type="support")
    assert claims["sub"] == str(customer.id) and claims["platform_actor_id"] == str(actor.id)
    for token_type in ("access", "refresh"):
        with pytest.raises(TokenInvalidError):
            decode_token(result["access_token"], expected_type=token_type)


async def test_every_allowed_business_get_keeps_real_auth_dependency():
    from app.main import app
    from app.modules.auth.deps import get_current_user

    def has_auth(dependant):
        return dependant.call is get_current_user or any(
            has_auth(dep) for dep in dependant.dependencies
        )

    routes = {
        route.path: route for route in app.routes if "GET" in getattr(route, "methods", set())
    }
    assert SUPPORT_READ_PATHS <= routes.keys()
    for path in SUPPORT_READ_PATHS:
        assert has_auth(routes[path].dependant), path


async def test_actor_denials_apply_to_permissions_fields_and_flow_without_changing_stored_view():
    from app.core.security.field_permissions import build_field_perm_context, can_read_field
    from app.core.tenancy import support_actor_revokes_ctx, support_context_ctx
    from app.modules.auth.service import AuthService
    from app.modules.flow.actor import load_flow_actor

    denied = frozenset({"blogger:read", "field.settlement.amount:read"})
    tokens = [
        support_readonly_ctx.set(True),
        support_context_ctx.set({"actor": {}}),
        support_actor_revokes_ctx.set(denied),
    ]
    role_repo = SimpleNamespace(list_codes_for_user=AsyncMock(return_value=["admin"]))
    # 客户只有角色通配，个人配置为空；排查约束不能修改它的实际覆盖。
    perm_repo = SimpleNamespace(list_scopes_for_user=AsyncMock(return_value=({"*"}, set(), set())))
    user_id = uuid4()
    try:
        service = AuthService(AsyncMock())
        service._permissions = perm_repo
        effective = await service.load_effective_permissions(user_id)
        assert not effective.has("blogger", "read") and effective.has("promotion", "read")
        fields = await build_field_perm_context(user_id, role_repo, perm_repo)
        assert not can_read_field("settlement", "amount", fields)
        assert can_read_field("settlement", "payment_amount", fields)
        flow = await load_flow_actor(user_id, role_repo, perm_repo)
        assert not flow.perms.has("blogger", "read")
        assert not can_read_field("settlement", "amount", flow.field_ctx)
        assert await perm_repo.list_scopes_for_user(user_id) == ({"*"}, set(), set())
    finally:
        for token in reversed(tokens):
            token.var.reset(token)
