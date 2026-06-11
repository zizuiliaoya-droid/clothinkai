"""U15 企微预警配置 API 契约测试（鉴权 401 + OpenAPI 路径）。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestWecomAlertApiContract:
    async def test_get_alert_config_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/wecom/alert-config")
        assert resp.status_code == 401

    async def test_put_alert_config_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.put("/api/wecom/alert-config", json={})
        assert resp.status_code == 401

    async def test_openapi_exposes_alert_config_endpoint(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/wecom/alert-config" in paths
        methods = paths["/api/wecom/alert-config"]
        assert "get" in methods
        assert "put" in methods
