"""集成测试：``GET /api/security/ip-diagnostics``。

权限用迁移 seed 出来的真实角色矩阵：建用户 → ``load_effective_permissions`` → override
``get_current_perms``。测到的是「真实角色 × 端点闸门」，不是手写的权限集合。

客户端一律 ``ASGITransport(client=("10.42.0.1", 12345))``：模拟生产上 Zeabur 入口连到容器、
TCP 对端是私网地址的情形。
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.security.permissions import EffectivePermissions
from app.modules.auth.deps import get_current_perms
from app.modules.auth.models import Role, Tenant
from app.modules.auth.service import AuthService
from app.modules.security.service import FORWARDING_HEADERS

PATH = "/api/security/ip-diagnostics"
PEER = ("10.42.0.1", 12345)


def _app() -> Any:
    from app.main import app

    return app


@pytest.fixture
def as_perms() -> Iterator[Callable[[EffectivePermissions], None]]:
    app = _app()

    def apply(perms: EffectivePermissions) -> None:
        app.dependency_overrides[get_current_perms] = lambda: perms

    yield apply
    # app 是模块级单例，不清理会污染后面的用例
    app.dependency_overrides.pop(get_current_perms, None)


async def _perms_for(
    session: AsyncSession, factory: Any, tenant: Tenant, role_code: str
) -> EffectivePermissions:
    role = (await session.execute(select(Role).where(Role.code == role_code))).scalar_one()
    user = await factory.user(tenant, roles=[role])
    return await AuthService(session).load_effective_permissions(user.id)


def _client(asgi_app: Any = None, peer: tuple[str, int] = PEER) -> AsyncClient:
    transport = ASGITransport(app=asgi_app or _app(), client=peer)
    return AsyncClient(transport=transport, base_url="http://test")


def _header_values(body: dict[str, Any]) -> dict[str, str | None]:
    return {h["name"]: h["value"] for h in body["headers"]}


FORGED = {
    "X-Forwarded-For": "203.0.113.9, 8.8.8.8",
    "X-Real-IP": "203.0.113.9",
    "Forwarded": "for=203.0.113.9;proto=https",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": "evil.example",
    "Via": "1.1 fake-proxy",
    "CF-Connecting-IP": "203.0.113.9",
    "True-Client-IP": "203.0.113.9",
}


@pytest.mark.integration
@pytest.mark.asyncio
class TestIpDiagnosticsEndpoint:
    @pytest.mark.parametrize("role_code", ["admin", "platform_admin"])
    async def test_system_admin_gets_200(
        self,
        session: AsyncSession,
        factory: Any,
        tenant_a: Tenant,
        as_perms: Callable[[EffectivePermissions], None],
        monkeypatch: pytest.MonkeyPatch,
        role_code: str,
    ) -> None:
        perms = await _perms_for(session, factory, tenant_a, role_code)
        assert "*" in perms.scopes  # 场景有效性：迁移 seed 的管理员确实持 *
        as_perms(perms)
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

        async with _client() as ac:
            resp = await ac.get(PATH)

        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "no-store"
        body = resp.json()
        assert body["client_host"] == "10.42.0.1"
        assert body["client_host_is_public"] is False
        assert [h["name"] for h in body["headers"]] == list(FORWARDING_HEADERS)
        assert all(h["value"] is None for h in body["headers"])
        assert body["xff_chain"] == []
        assert body["forwarded_allow_ips_set"] is False
        assert body["forwarded_allow_ips"] is None
        assert body["uvicorn_version"] == importlib.metadata.version("uvicorn")
        server_time = datetime.fromisoformat(body["server_time"])
        assert server_time.tzinfo is not None
        assert abs((datetime.now(UTC) - server_time).total_seconds()) < 60

    async def test_forged_headers_echoed_verbatim_client_host_not_overwritten(
        self,
        session: AsyncSession,
        factory: Any,
        tenant_a: Tenant,
        as_perms: Callable[[EffectivePermissions], None],
    ) -> None:
        as_perms(await _perms_for(session, factory, tenant_a, "admin"))

        async with _client() as ac:
            resp = await ac.get(PATH, headers=FORGED)

        assert resp.status_code == 200
        body = resp.json()
        assert _header_values(body) == FORGED
        assert body["xff_chain"] == [
            {"raw": "203.0.113.9", "ip": "203.0.113.9", "is_public": False},  # 文档段
            {"raw": "8.8.8.8", "ip": "8.8.8.8", "is_public": True},
        ]
        # 伪造头只回显，不会改写系统认定的客户端地址
        assert body["client_host"] == "10.42.0.1"
        assert body["client_host_is_public"] is False

    async def test_repeated_xff_lines_joined(
        self,
        session: AsyncSession,
        factory: Any,
        tenant_a: Tenant,
        as_perms: Callable[[EffectivePermissions], None],
    ) -> None:
        as_perms(await _perms_for(session, factory, tenant_a, "admin"))

        async with _client() as ac:
            resp = await ac.get(
                PATH, headers=[("X-Forwarded-For", "1.1.1.1"), ("X-Forwarded-For", "8.8.8.8")]
            )

        assert resp.status_code == 200
        body = resp.json()
        assert _header_values(body)["X-Forwarded-For"] == "1.1.1.1, 8.8.8.8"
        assert [hop["ip"] for hop in body["xff_chain"]] == ["1.1.1.1", "8.8.8.8"]

    @pytest.mark.parametrize(
        ("role_code", "known_scope"),
        [
            ("pr", "promotion.*:*"),
            ("pr_manager", "promotion.review:approve"),
            ("finance", "settlement:read"),
            ("operations", "report.*:read"),
        ],
    )
    async def test_other_roles_get_403(
        self,
        session: AsyncSession,
        factory: Any,
        tenant_a: Tenant,
        as_perms: Callable[[EffectivePermissions], None],
        role_code: str,
        known_scope: str,
    ) -> None:
        perms = await _perms_for(session, factory, tenant_a, role_code)
        # 场景有效性：权限确实从迁移加载出来了，且不是管理员
        assert perms.scopes
        assert "*" not in perms.scopes
        assert known_scope in perms.scopes
        as_perms(perms)

        async with _client() as ac:
            resp = await ac.get(PATH, headers=FORGED)

        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "PERMISSION_DENIED"
        assert body["details"]["required_scope"] == "security.ip_allowlist"
        assert body["details"]["required_action"] == "write"
        # 原始头只给管理员看
        assert "headers" not in body
        assert "203.0.113.9" not in resp.text


@pytest.mark.integration
@pytest.mark.asyncio
class TestUvicornDefaultTrust:
    """刻画 uvicorn ``--proxy-headers`` 在没配 FORWARDED_ALLOW_IPS 时的行为（默认只信任 127.0.0.1）。

    说明生产为什么只看到入口地址：入口连到容器时对端是私网地址，不在信任名单里，XFF 被忽略。
    对端被信任时，uvicorn 从 XFF 右往左取第一个不可信地址（0.30.6 源码
    ``ProxyHeadersMiddleware.get_trusted_client_host``；0.31+ 非 ``*`` 模式同样如此）。
    """

    async def test_untrusted_peer_keeps_peer_address(
        self,
        session: AsyncSession,
        factory: Any,
        tenant_a: Tenant,
        as_perms: Callable[[EffectivePermissions], None],
    ) -> None:
        as_perms(await _perms_for(session, factory, tenant_a, "admin"))
        wrapped = ProxyHeadersMiddleware(_app())  # trusted_hosts 用默认值 "127.0.0.1"

        async with _client(wrapped, PEER) as ac:
            resp = await ac.get(PATH, headers={"X-Forwarded-For": "203.0.113.9, 8.8.8.8"})

        assert resp.status_code == 200
        assert resp.json()["client_host"] == "10.42.0.1"

    async def test_trusted_peer_takes_rightmost_untrusted_hop(
        self,
        session: AsyncSession,
        factory: Any,
        tenant_a: Tenant,
        as_perms: Callable[[EffectivePermissions], None],
    ) -> None:
        as_perms(await _perms_for(session, factory, tenant_a, "admin"))
        wrapped = ProxyHeadersMiddleware(_app())

        async with _client(wrapped, ("127.0.0.1", 12345)) as ac:
            resp = await ac.get(PATH, headers={"X-Forwarded-For": "203.0.113.9, 8.8.8.8"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["client_host"] == "8.8.8.8"
        assert body["client_host_is_public"] is True
        # 改写的只是 client.host，原始头照样原样回显
        assert _header_values(body)["X-Forwarded-For"] == "203.0.113.9, 8.8.8.8"
