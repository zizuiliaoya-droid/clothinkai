"""API 测试：U10b 平台商品映射端点（契约级）。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestPlatformProductApiContract:
    async def test_create_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/platform-products/",
                json={"platform": "qianniu", "platform_id": "1", "style_id": str(uuid4())},
            )
        assert resp.status_code == 401

    async def test_list_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/platform-products/")
        assert resp.status_code == 401

    async def test_endpoints_in_openapi(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert "/api/platform-products/" in paths
        assert "/api/platform-products/lookup" in paths
        assert "/api/platform-products/{pp_id}" in paths
