"""U11 博主智能标签 API 契约测试（recompute 端点鉴权 + OpenAPI）."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestBloggerTagApiContract:
    async def test_recompute_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/bloggers/recompute-tags")
        assert resp.status_code == 401

    async def test_openapi_exposes_recompute(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/bloggers/recompute-tags" in paths

    async def test_openapi_response_has_audience_fields(self) -> None:
        """BloggerResponse schema 暴露 U11 新增字段."""
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        spec = resp.json()
        schema = spec.get("components", {}).get("schemas", {}).get(
            "BloggerResponse", {}
        )
        props = schema.get("properties", {})
        assert "audience_profile" in props
        assert "read_like_ratio" in props
