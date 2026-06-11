"""U17 套装/BI/导出 API 契约测试（鉴权 401 + OpenAPI 路径）。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestBundleExportApiContract:
    async def test_create_bundle_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/bundles/", json={})
        assert resp.status_code == 401

    async def test_bi_dashboard_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/reports/bi")
        assert resp.status_code == 401

    async def test_export_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/reports/production/export")
        assert resp.status_code == 401

    async def test_openapi_exposes_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/bundles/" in paths
        assert "/api/bundles/{bundle_id}" in paths
        assert "/api/reports/bi" in paths
        assert "/api/reports/bi/layout" in paths
        assert "/api/reports/{report_type}/export" in paths
