"""API 测试：U10a design 端点（契约级，无真实 DB）。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestDesignApiContract:
    async def test_create_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/designs/", json={"style_code": "X", "style_name": "Y"})
        assert resp.status_code == 401

    async def test_list_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/designs/")
        assert resp.status_code == 401

    async def test_confirm_price_requires_auth(self) -> None:
        from app.main import app

        sid = uuid4()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.put(f"/api/designs/{sid}/confirm-price")
        assert resp.status_code == 401

    async def test_endpoints_in_openapi(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        for p in (
            "/api/designs/",
            "/api/designs/{style_id}",
            "/api/designs/{style_id}/fabric",
            "/api/designs/{style_id}/grading",
            "/api/designs/{style_id}/complete",
            "/api/designs/{style_id}/confirm-price",
            "/api/designs/{style_id}/reject",
            "/api/designs/{style_id}/cancel",
        ):
            assert p in paths
