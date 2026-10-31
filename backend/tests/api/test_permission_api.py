"""API 测试：U09 自定义权限端点（契约级，无真实 DB）。

- grant / revoke / effective-permissions 无 token → 401
- 端点出现在 OpenAPI spec
- 错误响应统一格式
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestPermissionApiContract:
    @pytest.fixture(autouse=True)
    def _disable_external_rate_limit(self, monkeypatch):
        # 本文件只验权限端点契约，不依赖 Redis 限流服务。
        from app.main import app

        monkeypatch.setattr(app.state.limiter, "enabled", False)

    async def test_role_capabilities_require_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/users/capabilities")
        assert resp.status_code == 401

    async def test_catalog_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/permissions")
        assert resp.status_code == 401

    async def test_reset_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/users/{uuid4()}/permissions/reset",
                json={"scope": "negotiation_all:read"},
            )
        assert resp.status_code == 401

    async def test_grant_requires_auth(self) -> None:
        from app.main import app

        uid = uuid4()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/users/{uid}/permissions/grant",
                json={"scope": "field.sku.cost_price:read"},
            )
        assert resp.status_code == 401

    async def test_revoke_requires_auth(self) -> None:
        from app.main import app

        uid = uuid4()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/users/{uid}/permissions/revoke",
                json={"scope": "field.sku.cost_price:read"},
            )
        assert resp.status_code == 401

    async def test_effective_permissions_requires_auth(self) -> None:
        from app.main import app

        uid = uuid4()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/users/{uid}/effective-permissions")
        assert resp.status_code == 401

    async def test_endpoints_in_openapi(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert "/api/users/{user_id}/permissions/grant" in paths
        assert "/api/users/{user_id}/permissions/revoke" in paths
        assert "/api/users/{user_id}/effective-permissions" in paths
        assert "/api/permissions" in paths
        assert "/api/users/{user_id}/permissions/reset" in paths
