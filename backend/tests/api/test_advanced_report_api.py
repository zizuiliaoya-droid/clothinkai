"""U14 报表进阶 API 契约测试（6 端点鉴权 401 + OpenAPI 路径）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestAdvancedReportApiContract:
    async def test_work_progress_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/reports/work-progress?month=2026-05")
        assert resp.status_code == 401

    async def test_set_target_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/reports/targets", json={})
        assert resp.status_code == 401

    async def test_store_daily_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/reports/store-daily")
        assert resp.status_code == 401

    async def test_production_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/reports/production")
        assert resp.status_code == 401

    async def test_openapi_exposes_advanced_report_endpoints(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/reports/work-progress" in paths
        assert "/api/reports/targets" in paths
        assert "/api/reports/store-daily" in paths
        assert "/api/reports/store-daily/{day}" in paths
        assert "/api/reports/production" in paths

    async def test_store_daily_granularity_is_optional(self) -> None:
        """周 / 月 / 年改由后端分桶（7a-6）：参数可选、默认按日（旧前端不传，行为不变）。"""
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        operation = resp.json()["paths"]["/api/reports/store-daily"]["get"]
        params = {p["name"]: p for p in operation["parameters"]}
        assert params["granularity"].get("required", False) is False
        assert params["granularity"]["schema"]["default"] == "day"
        assert params["granularity"]["schema"]["pattern"] == "^(day|week|month|year)$"

    async def test_store_daily_forwards_granularity(self) -> None:
        from app.modules.report import advanced_api

        calls: list[tuple[object, ...]] = []

        class FakeStoreDailyService:
            async def get_dashboard(self, *args: object, **kwargs: object) -> list[object]:
                calls.append((*args, kwargs))
                return []

        tenant_id = uuid4()
        await advanced_api.get_store_daily(
            user=SimpleNamespace(tenant_id=tenant_id),  # type: ignore[arg-type]
            service=FakeStoreDailyService(),  # type: ignore[arg-type]
            preset="custom",
            date_from=date(2026, 6, 1),
            date_to=date(2026, 6, 30),
            granularity="week",
        )
        assert calls == [
            (tenant_id, (date(2026, 6, 1), date(2026, 6, 30)), {"granularity": "week"})
        ]

    async def test_openapi_exposes_store_report_pages(self) -> None:
        """7e R4：日 / 周 / 月三个新接口只有 GET；旧 /store-daily 标 deprecated（保留一个版本）。"""
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        paths = resp.json()["paths"]
        for page in ("daily", "weekly", "monthly"):
            assert set(paths[f"/api/reports/store/{page}"]) == {"get"}, page
            ref = paths[f"/api/reports/store/{page}"]["get"]["responses"]["200"]["content"]
            assert ref["application/json"]["schema"]["$ref"].endswith("/StoreReport"), page
        assert paths["/api/reports/store-daily"]["get"].get("deprecated") is True
        assert not paths["/api/reports/store/daily"]["get"].get("deprecated", False)

    @pytest.mark.parametrize("page", ["daily", "weekly", "monthly"])
    async def test_store_report_requires_auth(self, page: str) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get(f"/api/reports/store/{page}")
        assert resp.status_code == 401

    @pytest.mark.parametrize(
        ("handler", "granularity"),
        [
            ("get_store_report_daily", "day"),
            ("get_store_report_weekly", "week"),
            ("get_store_report_monthly", "month"),
        ],
    )
    async def test_store_report_forwards_granularity(self, handler: str, granularity: str) -> None:
        from app.modules.report import advanced_api

        calls: list[tuple[object, ...]] = []

        class FakeStoreDailyService:
            async def get_store_report(self, *args: object) -> object:
                calls.append(args)
                return "report"

        tenant_id = uuid4()
        result = await getattr(advanced_api, handler)(
            user=SimpleNamespace(tenant_id=tenant_id),
            service=FakeStoreDailyService(),
            preset="custom",
            date_from=date(2026, 6, 3),
            date_to=date(2026, 6, 30),
        )
        assert result == "report"
        assert calls == [(tenant_id, (date(2026, 6, 3), date(2026, 6, 30)), granularity)]

    async def test_summary_refresh_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/reports/summaries/refresh?date_from=2026-03-01&date_to=2026-03-31"
            )
        assert resp.status_code == 401

    async def test_summary_freshness_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/reports/summaries/freshness?preset=last_7d")
        assert resp.status_code == 401

    async def test_openapi_exposes_summary_freshness(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        paths = resp.json().get("paths", {})
        assert set(paths["/api/reports/summaries/freshness"]) == {"get"}

    async def test_openapi_exposes_summary_refresh(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/reports/summaries/refresh" in paths
        # 只能 POST：GET 一个会删重建数据的端点容易被预取/爬虫误触发
        assert set(paths["/api/reports/summaries/refresh"]) == {"post"}
