"""类目下线（8a-3，FR-3，J12，AC 15、18）。

- 路由表：投产、投产导出、BI、商品列表都不再声明 ``category`` 查询参数
  （**不对 ``/api/styles/`` 断言**——款式列表的类目筛选参数保留，W1 在改该接口）
- 投产接口带 ``category=…`` 与不带的结果完全一致：实时与汇总两条读路径各一次（AC 18）
- 商品接口不再返回 / 接收 ``category``（AC 15 的后端部分；``goods_main.category`` 列保留）
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi.dependencies.models import Dependant
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.modules.report.production_service as production_mod
from app.core.config import settings
from app.core.tenancy import tenant_id_ctx
from app.modules.product.goods_repository import GoodsListFilters
from app.modules.product.goods_schemas import (
    GoodsMainCreate,
    GoodsMainResponse,
    GoodsMainUpdate,
    GoodsStyleItemIn,
)
from app.modules.product.goods_service import GoodsService
from tests.integration.test_summary_read_equivalence import M_HI, M_LO, _refresh, _seed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _query_param_names(dependant: Dependant) -> set[str]:
    """路由及其子依赖声明的全部查询参数名。"""
    names = {p.alias for p in dependant.query_params}
    for sub in dependant.dependencies:
        names |= _query_param_names(sub)
    return names


def _route(path: str, method: str = "GET") -> Any:
    from fastapi.routing import APIRoute

    from app.main import app

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == path and method in route.methods:
            return route
    raise AssertionError(f"路由不存在：{method} {path}")


class TestRoutesNoCategory:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/reports/production",
            "/api/reports/{report_type}/export",
            "/api/reports/bi",
            "/api/goods/",
        ],
    )
    def test_no_category_query_param(self, path: str) -> None:
        names = _query_param_names(_route(path).dependant)
        assert "category" not in names
        assert "categories" not in names

    def test_production_still_filters_by_season(self) -> None:
        """场景有效性：参数名确实取到了（不是空集合碰巧不含 category）。"""
        assert "season" in _query_param_names(_route("/api/reports/production").dependant)
        assert "season" in _query_param_names(_route("/api/reports/{report_type}/export").dependant)


class _SourceRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, report: str, source: str) -> None:
        self.calls.append((report, source))


class TestProductionIgnoresCategory:
    async def test_with_and_without_category_identical(
        self,
        session: AsyncSession,
        tenant_a: Any,
        admin_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """AC 18：旧客户端带 category 不报错、不生效——实时与汇总两条读路径都与不带一致。"""
        from httpx import ASGITransport, AsyncClient

        from app.core.db import get_session
        from app.main import app
        from app.modules.auth.deps import get_current_perms, get_current_user_active
        from app.modules.auth.service import AuthService

        recorder = _SourceRecorder()
        monkeypatch.setattr(production_mod, "record_source", recorder)

        async def _session_override() -> AsyncIterator[AsyncSession]:
            yield session

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory, factory
            )
            # 库里的类目值保留：两个商品类目不同，若还按类目筛，带 category=外套 只会剩 B
            await session.execute(
                text("UPDATE goods_main SET category = :c WHERE id = :id"),
                {"c": "连衣裙", "id": seeded["goods_a"].id},
            )
            await session.execute(
                text("UPDATE goods_main SET category = :c WHERE id = :id"),
                {"c": "外套", "id": seeded["goods_b"].id},
            )
            user = await factory.user(tenant_a, roles=[admin_role])
            perms = await AuthService(session).load_effective_permissions(user.id)
            await session.commit()

            app.dependency_overrides[get_session] = _session_override
            app.dependency_overrides[get_current_user_active] = lambda: user
            app.dependency_overrides[get_current_perms] = lambda: perms
            base = {"preset": "custom", "date_from": str(M_LO), "date_to": str(M_HI)}

            async def fetch(client: AsyncClient, **extra: Any) -> Any:
                resp = await client.get("/api/reports/production", params={**base, **extra})
                assert resp.status_code == 200, resp.text
                return resp.json()

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                # 实时路径：关掉汇总读取
                monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", False)
                live_plain = await fetch(c)
                live_cat = await fetch(c, category="外套")
                live_multi = await fetch(c, category=["外套", "不存在的类目"])
                assert ("production", "live") in recorder.calls
                assert ("production", "summary") not in recorder.calls
                assert len(live_plain["items"]) == 2, "场景没有产生两个商品的投产行"
                assert live_cat == live_plain
                assert live_multi == live_plain

                # 汇总路径：刷新覆盖本期后再读
                monkeypatch.setattr(settings, "REPORT_SUMMARY_READS_ENABLED", True)
                await _refresh(session, tenant_a, M_LO, M_HI)
                recorder.calls.clear()
                sum_plain = await fetch(c)
                sum_cat = await fetch(c, category="外套")
                assert ("production", "summary") in recorder.calls
                assert sum_cat == sum_plain
                assert sum_plain["items"] == live_plain["items"]

                # 投产导出同理：带 category 不报错
                exp = await c.get(
                    "/api/reports/production/export", params={**base, "category": "外套"}
                )
                assert exp.status_code == 200, exp.text
        finally:
            for dep in (get_session, get_current_user_active, get_current_perms):
                app.dependency_overrides.pop(dep, None)
            tenant_id_ctx.reset(token)


class TestGoodsNoCategory:
    def test_schemas_have_no_category(self) -> None:
        assert "category" not in GoodsMainCreate.model_fields
        assert "category" not in GoodsMainUpdate.model_fields
        assert "category" not in GoodsMainResponse.model_fields
        assert not hasattr(GoodsListFilters(), "category")

    async def test_category_ignored_on_write_and_absent_on_read(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        product_factory: Any,
    ) -> None:
        """传了 category 被忽略：新建不写、更新不改；列与存量值保留，接口不再返回。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await factory.user(tenant_a, roles=[admin_role])
            style = await product_factory.style(style_code="CAT-S1")
            svc = GoodsService(session)
            created = await svc.create(
                GoodsMainCreate.model_validate(
                    {
                        "goods_code": "CAT-G1",
                        "goods_title": "类目测试",
                        "category": "外套",
                        "items": [GoodsStyleItemIn(style_id=style.id)],
                    }
                ),
                tenant_id=tenant_a.id,
                user_id=user.id,
            )
            raw = (
                await session.execute(
                    text("SELECT category FROM goods_main WHERE id = :id"), {"id": created.id}
                )
            ).scalar_one()
            assert raw is None
            # 存量值保留：直接写库模拟历史数据，更新时传别的类目也不动它
            await session.execute(
                text("UPDATE goods_main SET category = '历史类目' WHERE id = :id"),
                {"id": created.id},
            )
            updated = await svc.update(
                created.id,
                GoodsMainUpdate.model_validate({"category": "上衣", "season": "2026秋"}),
                user_id=user.id,
            )
            assert updated.season == "2026秋"
            raw = (
                await session.execute(
                    text("SELECT category FROM goods_main WHERE id = :id"), {"id": created.id}
                )
            ).scalar_one()
            assert raw == "历史类目"
            assert "category" not in updated.model_dump()
            assert "category" not in (await svc.get(created.id)).model_dump()
            rows, _ = await svc.list_goods(
                tenant_id=tenant_a.id, filters=GoodsListFilters(keyword="CAT-G1")
            )
            assert [r.goods_code for r in rows] == ["CAT-G1"]
            assert "category" not in rows[0].model_dump()
        finally:
            tenant_id_ctx.reset(token)
