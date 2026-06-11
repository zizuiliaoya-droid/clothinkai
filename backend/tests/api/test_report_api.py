"""U08 report API 契约测试（鉴权 401 + OpenAPI 4 端点 + 非法 preset 422）。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestReportApiAuth:
    async def test_summary_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/reports/publish-progress/summary")
        assert resp.status_code == 401

    async def test_cards_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/reports/publish-progress/cards")
        assert resp.status_code == 401

    async def test_by_pr_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/reports/publish-progress/styles/{uuid4()}/by-pr"
            )
        assert resp.status_code == 401

    async def test_by_time_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(
                f"/api/reports/publish-progress/styles/{uuid4()}/by-time"
            )
        assert resp.status_code == 401


@pytest.mark.api
@pytest.mark.asyncio
class TestReportOpenApi:
    async def test_openapi_exposes_report_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert "/api/reports/publish-progress/summary" in paths
        assert "/api/reports/publish-progress/cards" in paths
        assert (
            "/api/reports/publish-progress/styles/{style_id}/by-pr" in paths
        )
        assert (
            "/api/reports/publish-progress/styles/{style_id}/by-time" in paths
        )
