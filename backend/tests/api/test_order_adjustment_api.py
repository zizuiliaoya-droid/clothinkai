"""U16 拍单/刷单/余额 API 契约测试（鉴权 401 + OpenAPI 路径）。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestOrderAdjustmentApiContract:
    async def test_create_brushing_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/finance/order-adjustments/brushing", json={}
            )
        assert resp.status_code == 401

    async def test_add_balance_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/finance/balance-records", json={})
        assert resp.status_code == 401

    async def test_list_order_adjustments_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/finance/order-adjustments")
        assert resp.status_code == 401

    async def test_openapi_exposes_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/finance/order-adjustments/brushing" in paths
        assert "/api/finance/order-adjustments" in paths
        assert "/api/finance/balance-records" in paths
