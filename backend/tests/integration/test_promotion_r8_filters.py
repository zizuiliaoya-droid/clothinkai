"""R8 真 SQL / HTTP 合同；需隔离 PostgreSQL，不以本机静态检查代替。"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.metric_config.settings import PlatformMetricSettings
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.promotion.repository import PromotionListFilters, PromotionRepository
from app.modules.promotion.urge_calculator import get_today
from tests.api.test_report_drill_api import _call

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_attribution_pr_intersection_pagination_and_options(
    session: AsyncSession,
    tenant_a: Any,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> None:
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        style = await product_factory.style()
        bare = await product_factory.style()
        blogger = await blogger_factory.blogger()
        goods = []
        for code, season, suit in [
            ("R8-A", "春季", False),
            ("R8-B", "夏季", True),
            ("R8-C", "无关季", False),
        ]:
            g = GoodsMain(
                tenant_id=tenant_a.id,
                goods_code=code,
                goods_title=code,
                season=season,
                is_suit=suit,
            )
            session.add(g)
            await session.flush()
            goods.append(g)
            if code != "R8-C":
                session.add(
                    GoodsStyleItem(
                        tenant_id=tenant_a.id, goods_main_id=g.id, style_id=style.id, sort_order=0
                    )
                )
        await session.flush()
        a, b, unrelated = goods
        rows = []
        for kw in [
            {},
            {"goods_main_id": b.id, "pr": flow_users.pr},
            {},
            {"style": bare, "pr": flow_users.pr},
        ]:
            rows.append(
                await promotion_factory.promotion(
                    style=kw.pop("style", style),
                    blogger=blogger,
                    internal_code=f"R8-{uuid4().hex[:16]}",
                    **kw,
                )
            )
        for p in rows:
            p.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        await promotion_factory.promotion(
            style=style, blogger=blogger, is_active=False, goods_main_id=unrelated.id
        )
        await session.flush()

        async def listed(
            f: PromotionListFilters, page: int = 1, size: int = 100
        ) -> tuple[list[Any], int]:
            return await PromotionRepository(session).list_with_cte(
                tenant_id=tenant_a.id,
                filters=f,
                page=page,
                page_size=size,
                today=get_today(),
                urge_threshold_days=7,
                important_threshold_days=14,
                metric_settings=PlatformMetricSettings.defaults(),
            )

        inferred = PromotionListFilters(goods_main_id=a.id, seasons=["春季"], unassigned_only=True)
        got, total = await listed(inferred)
        expected = sorted([rows[0].id, rows[2].id], reverse=True)
        assert total == 2 and [r.promotion.id for r in got] == expected
        first, n1 = await listed(inferred, 1, 1)
        second, n2 = await listed(inferred, 2, 1)
        assert n1 == n2 == 2
        assert [first[0].promotion.id, second[0].promotion.id] == expected
        assert (await listed(PromotionListFilters(goods_main_id=b.id, seasons=["夏季"])))[1] == 1
        assert (await listed(PromotionListFilters(goods_main_id=b.id, seasons=["春季"])))[1] == 0
        assert (await listed(PromotionListFilters(pr_id=flow_users.pr.id, unassigned_only=True)))[
            1
        ] == 0
        assert (await listed(PromotionListFilters(unassigned_only=True)))[1] == 2

        response = await _call(
            session,
            flow_users.admin,
            f"/api/promotions/?goods_main_id={a.id}&seasons=春季&unassigned_only=true&page_size=1",
        )
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        assert response.json()["items"][0]["id"] == str(expected[0])
        for query in [
            "goods_main_id=bad",
            "seasons=",
            "seasons=" + "季" * 65,
            "&".join(["seasons=季"] * 21),
        ]:
            assert (
                await _call(session, flow_users.admin, f"/api/promotions/?{query}")
            ).status_code == 422

        url = "/api/promotions/filter-options"
        response = await _call(session, flow_users.admin, f"{url}?page_size=1&selected_id={b.id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == 2 and len(body["items"]) == 1
        assert body["selected"] == {"id": str(b.id), "display_name": "R8-B"}
        assert body["seasons"] == ["夏季", "春季"]
        assert set(body["items"][0]) == {"id", "display_name"}
        hidden = await _call(
            session, flow_users.admin, f"{url}?selected_id={unrelated.id}&keyword=不存在"
        )
        assert hidden.json()["selected"] is None and hidden.json()["items"] == []
        assert (await _call(session, flow_users.finance, url)).status_code == 403
        for query in ["page_size=101", "keyword=" + "x" * 65, "selected_id=bad"]:
            assert (await _call(session, flow_users.admin, f"{url}?{query}")).status_code == 422
    finally:
        tenant_id_ctx.reset(token)


async def test_other_tenant_selected_goods_not_disclosed(
    session: AsyncSession,
    tenant_a: Any,
    tenant_b: Any,
    flow_users: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> None:
    token = tenant_id_ctx.set(tenant_b.id)
    try:
        style = await product_factory.style(tenant=tenant_b)
        blogger = await blogger_factory.blogger(tenant=tenant_b)
        goods = GoodsMain(
            tenant_id=tenant_b.id,
            goods_code=f"R8B{uuid4().hex[:8]}",
            goods_title="另一租户",
            season="另一租户季节",
        )
        session.add(goods)
        await session.flush()
        await promotion_factory.promotion(
            tenant=tenant_b, style=style, blogger=blogger, goods_main_id=goods.id
        )
        gid = goods.id
    finally:
        tenant_id_ctx.reset(token)
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        response = await _call(
            session, flow_users.admin, f"/api/promotions/filter-options?selected_id={gid}"
        )
        assert response.status_code == 200, response.text
        assert response.json() == {
            "items": [],
            "total": 0,
            "page": 1,
            "page_size": 50,
            "selected": None,
            "seasons": [],
        }
    finally:
        tenant_id_ctx.reset(token)
