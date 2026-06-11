"""API 测试：/health（liveness）+ /ready（readiness）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestHealthEndpoints:
    async def test_health_returns_200(self) -> None:
        """Liveness 不查依赖，永远 200。"""
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    async def test_ready_when_all_healthy(self) -> None:
        """Readiness：DB + Redis 都健康 → 200。"""
        from app import main as main_module

        with patch.object(main_module, "check_db_health", AsyncMock(return_value=True)), patch.object(
            main_module, "check_redis_health", AsyncMock(return_value=True)
        ):
            async with AsyncClient(
                transport=ASGITransport(app=main_module.app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["db"] == "ok"
        assert body["checks"]["redis"] == "ok"

    async def test_ready_when_db_unhealthy(self) -> None:
        from app import main as main_module

        with patch.object(main_module, "check_db_health", AsyncMock(return_value=False)), patch.object(
            main_module, "check_redis_health", AsyncMock(return_value=True)
        ):
            async with AsyncClient(
                transport=ASGITransport(app=main_module.app), base_url="http://test"
            ) as ac:
                resp = await ac.get("/ready")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "error"
        assert body["checks"]["db"] == "error"

    async def test_request_id_header_echoed(self) -> None:
        """请求带 X-Request-ID 应被回显。"""
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/health", headers={"X-Request-ID": "test-rid-1"})
        assert resp.headers.get("X-Request-ID") == "test-rid-1"
