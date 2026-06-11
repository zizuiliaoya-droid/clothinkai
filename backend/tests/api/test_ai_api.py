"""U18 AI 决策建议 API 契约测试（鉴权 401 + OpenAPI 路径）。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestAiApiContract:
    async def test_strategy_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/ai/strategy-advice", json={})
        assert resp.status_code == 401

    async def test_anomaly_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/ai/anomaly-diagnosis", json={})
        assert resp.status_code == 401

    async def test_blogger_suggest_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/ai/blogger-suggest", json={})
        assert resp.status_code == 401

    async def test_openapi_exposes_ai_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/ai/strategy-advice" in paths
        assert "/api/ai/anomaly-diagnosis" in paths
        assert "/api/ai/blogger-suggest" in paths
