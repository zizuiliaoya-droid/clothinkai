"""API 测试：U10b 平台商品映射端点（契约级）。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.api
@pytest.mark.asyncio
class TestPlatformProductApiContract:
    async def test_create_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/platform-products/",
                json={"platform": "qianniu", "platform_id": "1", "style_id": str(uuid4())},
            )
        assert resp.status_code == 401

    async def test_list_requires_auth(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/platform-products/")
        assert resp.status_code == 401

    async def test_endpoints_in_openapi(self) -> None:
        from app.main import app

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert "/api/platform-products/" in paths
        assert "/api/platform-products/lookup" in paths
        assert "/api/platform-products/{pp_id}" in paths


@pytest.mark.api
@pytest.mark.asyncio
class TestPlatformLinkRefreshBusy:
    """改链接撞上正在跑的汇总刷新（7e-9，报表视图设计 3.3）：409、整事务回滚，链接没改、覆盖还在。

    刷新锁要另一条连接去占（测试 session 单连接里 advisory lock 可重入）。service 撞锁会
    ``rollback``（测试 session 里只回滚到上一个保存点），所以发请求前先 ``commit`` 落种子。
    """

    async def test_update_while_refreshing_returns_409_and_changes_nothing(
        self,
        session: Any,
        engine: Any,
        tenant_a: Any,
        factory: Any,
        product_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sqlalchemy import select, text
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        from app.core.db import get_session
        from app.core.tenancy import tenant_id_ctx
        from app.main import app
        from app.modules.auth.deps import get_current_perms, get_current_user_active
        from app.modules.auth.models import Role
        from app.modules.auth.service import AuthService
        from app.modules.product.platform_product_models import PlatformProduct
        from app.modules.report import summary_invalidation

        monkeypatch.setattr(summary_invalidation, "LOCK_TIMEOUT", "300ms")
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            link = PlatformProduct(
                tenant_id=tenant_a.id,
                platform="千牛",
                platform_id=str(10**12 + uuid4().int % 10**12),
                style_id=style.id,
                channel="普通",
            )
            session.add(link)
            await session.flush()
            await session.execute(
                text(
                    "INSERT INTO report_summary_coverage "
                    "(id, tenant_id, stat_date, refreshed_at, created_at, updated_at) "
                    "SELECT gen_random_uuid(), :t, d::date, NOW(), NOW(), NOW() "
                    "FROM generate_series(DATE '2026-03-01', DATE '2026-03-03', "
                    "interval '1 day') AS d"
                ),
                {"t": tenant_a.id},
            )
            role = (
                await session.execute(select(Role).where(Role.code == "operations"))
            ).scalar_one()
            user = await factory.user(tenant_a, roles=[role])
            perms = await AuthService(session).load_effective_permissions(user.id)
            link_id, tid = link.id, tenant_a.id
            await session.commit()

            async def _session_override() -> AsyncIterator[Any]:
                yield session

            app.dependency_overrides[get_session] = _session_override
            app.dependency_overrides[get_current_user_active] = lambda: user
            app.dependency_overrides[get_current_perms] = lambda: perms
            maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            async with maker() as holder:
                got = (
                    await holder.execute(
                        text(
                            "SELECT pg_try_advisory_xact_lock("
                            "hashtextextended(CAST(:key AS text), 0))"
                        ),
                        {"key": summary_invalidation.summary_lock_key(tid)},
                    )
                ).scalar_one()
                assert got is True
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.put(
                        f"/api/platform-products/{link_id}",
                        json={"is_active": False, "channel": "直播"},
                    )
                await holder.rollback()
            assert resp.status_code == 409, resp.text
            body = resp.json()
            assert body["code"] == "REPORT_SUMMARY_REFRESH_BUSY"
            assert body["message"] == "报表正在刷新，请稍后再保存"
            row = (
                await session.execute(
                    text("SELECT is_active, channel FROM platform_product WHERE id = :id"),
                    {"id": link_id},
                )
            ).one()
            assert (row.is_active, row.channel) == (True, "普通")
            covered = (
                await session.execute(
                    text("SELECT count(*) FROM report_summary_coverage WHERE tenant_id = :t"),
                    {"t": tid},
                )
            ).scalar_one()
            assert covered == 3
        finally:
            tenant_id_ctx.reset(token)
            for dep in (get_session, get_current_user_active, get_current_perms):
                app.dependency_overrides.pop(dep, None)
