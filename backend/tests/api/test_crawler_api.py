"""U13 采集 API 契约测试（Worker 鉴权矩阵 + 看板鉴权 + OpenAPI）。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestCrawlerApiContract:
    async def test_poll_requires_worker_token(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/crawler/tasks/poll")
        # 无 X-Worker-Token → 401
        assert resp.status_code == 401

    async def test_worker_token_admin_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/crawler/worker-tokens/",
                json={"name": "x", "ip_allowlist": []},
            )
        assert resp.status_code == 401

    async def test_data_quality_summary_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/data-quality/summary")
        assert resp.status_code == 401

    async def test_openapi_exposes_crawler_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/crawler/tasks/poll" in paths
        assert "/api/crawler/tasks/{task_id}/exchange" in paths
        assert "/api/crawler/tasks/{task_id}/result" in paths
        assert "/api/crawler/worker-tokens/" in paths
        assert "/api/data-quality/summary" in paths
        assert "/api/data-quality/issues" in paths
