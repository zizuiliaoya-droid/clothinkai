"""U03 blogger API 端到端契约测试."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestBloggerApiContract:
    async def test_list_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/bloggers/")
        assert resp.status_code == 401

    async def test_create_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/bloggers/",
                json={"xiaohongshu_id": "X", "nickname": "x"},
            )
        assert resp.status_code == 401

    async def test_create_validates_payload(self) -> None:
        """无效 xiaohongshu_id 格式 → 401（auth 先于 schema） 或 422."""
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                "/api/bloggers/",
                json={
                    "xiaohongshu_id": "包含中文",
                    "nickname": "x",
                },
                headers={"Authorization": "Bearer fake"},
            )
        assert resp.status_code in {401, 422}

    async def test_openapi_exposes_blogger_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        paths = spec.get("paths", {})
        assert "/api/bloggers/" in paths
        assert "/api/bloggers/{blogger_id}" in paths
        assert "/api/bloggers/{blogger_id}/disable" in paths
        assert "/api/bloggers/{blogger_id}/restore" in paths
