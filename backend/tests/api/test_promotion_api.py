"""U04 promotion API 端到端契约测试."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestPromotionApiContract:
    async def test_list_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/promotions/")
        assert resp.status_code == 401

    async def test_create_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post("/api/promotions/", json={})
        assert resp.status_code == 401

    async def test_publish_requires_auth(self) -> None:
        from app.main import app
        from uuid import uuid4

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/promotions/{uuid4()}/publish",
                json={
                    "publish_url": "https://x.com/n",
                    "actual_publish_date": "2026-05-28",
                },
            )
        assert resp.status_code == 401

    async def test_review_requires_auth(self) -> None:
        from app.main import app
        from uuid import uuid4

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.post(
                f"/api/promotions/{uuid4()}/review",
                json={"action": "approve"},
            )
        assert resp.status_code == 401

    async def test_openapi_exposes_promotion_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        spec = resp.json()
        paths = spec.get("paths", {})
        assert "/api/promotions/" in paths
        assert "/api/promotions/{promotion_id}" in paths
        assert "/api/promotions/{promotion_id}/publish" in paths
        assert "/api/promotions/{promotion_id}/cancel" in paths
        assert "/api/promotions/{promotion_id}/recall/start" in paths
        assert "/api/promotions/{promotion_id}/recall/success" in paths
        assert "/api/promotions/{promotion_id}/recall/failure" in paths
        assert "/api/promotions/{promotion_id}/review" in paths
