"""U05 settlement API 端到端契约测试（鉴权 + OpenAPI 8 端点 + DELETE 405）."""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestSettlementApiAuth:
    async def test_list_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/settlements/")
        assert resp.status_code == 401

    async def test_get_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get(f"/api/settlements/{uuid4()}")
        assert resp.status_code == 401

    async def test_review_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.put(
                f"/api/settlements/{uuid4()}/review",
                json={"action": "approve"},
            )
        assert resp.status_code == 401

    async def test_payment_proof_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.put(
                f"/api/settlements/{uuid4()}/payment-proof",
                json={
                    "payment_date": "2026-05-26",
                    "payment_proof_attachment_id": str(uuid4()),
                },
            )
        assert resp.status_code == 401

    async def test_daily_summary_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/settlements/daily-summary/as-of")
        assert resp.status_code == 401


@pytest.mark.api
@pytest.mark.asyncio
class TestSettlementApiDeleteForbidden:
    """FB3：DELETE → 405（财务记录永久不可删除）。"""

    async def test_delete_returns_405(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.delete(f"/api/settlements/{uuid4()}")
        # DELETE 端点硬编码 405（不经鉴权依赖，直接拒绝方法）
        assert resp.status_code == 405


@pytest.mark.api
@pytest.mark.asyncio
class TestSettlementOpenApi:
    async def test_openapi_exposes_settlement_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/settlements/" in paths
        assert "/api/settlements/{settlement_id}" in paths
        assert "/api/settlements/{settlement_id}/review" in paths
        assert "/api/settlements/{settlement_id}/extra-items" in paths
        assert "/api/settlements/{settlement_id}/payment-amount" in paths
        assert "/api/settlements/{settlement_id}/payment-proof" in paths
        assert "/api/settlements/daily-summary/as-of" in paths
        assert "/api/settlements/daily-summary/activity" in paths

    async def test_openapi_exposes_attachment_endpoints(self) -> None:
        """shared attachment 基础设施端点也应暴露。"""
        from app.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            resp = await ac.get("/api/openapi.json")
        paths = resp.json().get("paths", {})
        assert "/api/attachments/upload-init" in paths
        assert "/api/attachments/{attachment_id}/complete" in paths
