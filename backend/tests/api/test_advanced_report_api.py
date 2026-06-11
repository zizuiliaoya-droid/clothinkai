"""U14 报表进阶 API 契约测试（6 端点鉴权 401 + OpenAPI 路径）。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestAdvancedReportApiContract:
    async def test_work_progress_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/reports/work-progress?month=2026-05")
        assert resp.status_code == 401

    async def test_set_target_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/reports/targets", json={})
        assert resp.status_code == 401

    async def test_store_daily_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/reports/store-daily")
        assert resp.status_code == 401

    async def test_production_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/reports/production")
        assert resp.status_code == 401

    async def test_openapi_exposes_advanced_report_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/reports/work-progress" in paths
        assert "/api/reports/targets" in paths
        assert "/api/reports/store-daily" in paths
        assert "/api/reports/store-daily/{day}" in paths
        assert "/api/reports/production" in paths
