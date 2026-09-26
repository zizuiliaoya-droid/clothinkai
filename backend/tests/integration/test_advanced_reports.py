"""U14 报表进阶集成测试。

覆盖：工作进度月度聚合 / 爆款约篇 set+list 达标 / 店铺聚合+手动 upsert /
投产跨表+周环比 / RLS 隔离。

测试引擎用 bypass 角色（RLS OFF）→ 聚合查询已显式 WHERE tenant_id。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import AdDaily, QianniuDaily
from app.modules.finance.order_adjustment_models import OrderAdjustment
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.advanced_repository import ProductionRepository
from app.modules.report.advanced_schemas import (
    StoreDailyManualUpdate,
    TargetCreate,
)
from app.modules.report.production_service import ProductionService
from app.modules.report.store_daily_service import StoreDailyService
from app.modules.report.target_planning_service import TargetPlanningService
from app.modules.report.work_progress_service import WorkProgressService

pytestmark = pytest.mark.asyncio


async def _goods(
    session: AsyncSession,
    tenant: Any,
    *styles: Any,
    code: str | None = None,
    is_suit: bool = False,
) -> GoodsMain:
    """建一个商品并挂上款式，复刻 037/038/040 回填后的生产形态。

    投产报表按商品聚合，款式必须先归到商品才会出现在报表里。
    """
    goods = GoodsMain(
        tenant_id=tenant.id,
        goods_code=code or f"G{uuid4().hex[:8]}",
        goods_title=styles[0].style_name if styles else "测试商品",
        is_suit=is_suit,
    )
    session.add(goods)
    await session.flush()
    for idx, style in enumerate(styles):
        session.add(
            GoodsStyleItem(
                tenant_id=tenant.id,
                goods_main_id=goods.id,
                style_id=style.id,
                sort_order=idx,
            )
        )
    await session.flush()
    return goods


async def _platform_product(
    session: AsyncSession,
    tenant: Any,
    style: Any,
    platform: str = "千牛",
    goods: GoodsMain | None = None,
) -> PlatformProduct:
    if goods is None:
        goods = await _goods(session, tenant, style)
    pp = PlatformProduct(
        tenant_id=tenant.id,
        platform=platform,
        platform_id=f"P{uuid4().hex[:8]}",
        style_id=style.id,
        goods_main_id=goods.id,
    )
    session.add(pp)
    await session.flush()
    return pp


async def _qianniu(
    session: AsyncSession,
    tenant: Any,
    pp: PlatformProduct,
    day: date,
    *,
    pay: str = "1000.00",
    visitors: int = 100,
    orders: int = 10,
    extra: dict | None = None,
) -> None:
    session.add(
        QianniuDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            visitors=visitors,
            pay_amount=Decimal(pay),
            pay_orders=orders,
            extra=extra,
        )
    )
    await session.flush()


async def _ad(
    session: AsyncSession,
    tenant: Any,
    pp: PlatformProduct,
    day: date,
    *,
    cost: str = "200.00",
) -> None:
    session.add(
        AdDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            cost=Decimal(cost),
        )
    )
    await session.flush()


class TestWorkProgress:
    async def test_month_aggregation_by_pr(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            # 同 PR 本月 2 篇：1 已发布(含 like) + 1 未发布
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                cooperation_date=date(2026, 5, 10),
                publish_status="已发布",
                like_count=600,
                cost_snapshot=Decimal("300"),
            )
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                cooperation_date=date(2026, 5, 20),
                publish_status="未发布",
            )
            # 上月 1 篇（不应计入 2026-05）
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                cooperation_date=date(2026, 4, 15),
                publish_status="已发布",
                like_count=10,
            )
            await session.commit()

            rows = await WorkProgressService(session).get_for_month(tenant_a.id, "2026-05")
            mine = [r for r in rows if r.pr_id == pr.id]
            assert len(mine) == 1
            row = mine[0]
            assert row.quote_count == 2
            assert row.publish_count == 1
            assert row.hit_count == 1  # like 600 >= HIT_STAT_THRESHOLD(500)
            assert row.effective_quote_count == 2  # 无召回、无取消
            assert row.month_complete_rate == Decimal("0.5000")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_complete_and_overdue_rate_exclude_recall_and_cancel(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """PRD 第 9 章：完成率/超时率分母 = 约稿量 − 召回量 − 取消量。

        用裸约稿量做分母会把已取消、已召回的单也当成「还能发布」，完成率被系统性低估。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            common = {"style": style, "blogger": blogger, "pr": pr}
            day = date(2026, 6, 10)

            # 有效 2 单：1 已发布 + 1 未发布
            await promotion_factory.promotion(
                **common, cooperation_date=day, publish_status="已发布", like_count=10
            )
            await promotion_factory.promotion(
                **common, cooperation_date=day, publish_status="未发布"
            )
            # 取消 1 单
            await promotion_factory.promotion(
                **common, cooperation_date=day, publish_status="已取消"
            )
            # 召回 1 单
            await promotion_factory.promotion(
                **common, cooperation_date=day, publish_status="未发布", recall_status="召回成功"
            )
            # 既取消又召回 1 单 —— 相减法会把它扣两次，FILTER 只扣一次
            await promotion_factory.promotion(
                **common, cooperation_date=day, publish_status="已取消", recall_status="召回成功"
            )
            await session.commit()

            rows = await WorkProgressService(session).get_for_month(tenant_a.id, "2026-06")
            row = next(r for r in rows if r.pr_id == pr.id)

            assert row.quote_count == 5
            assert row.cancel_count == 2
            assert row.recall_due_count == 2
            # 5 单里只有 2 单既未取消也未召回
            assert row.effective_quote_count == 2
            # 完成率 = 1 已发布 / 2 有效，而不是 1/5
            assert row.month_complete_rate == Decimal("0.5000")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_rates_are_null_when_all_recalled_or_cancelled(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """有效约稿量被扣成 0 时，比率置空而不是报除零。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                cooperation_date=date(2026, 7, 5),
                publish_status="已取消",
            )
            await session.commit()

            rows = await WorkProgressService(session).get_for_month(tenant_a.id, "2026-07")
            row = next(r for r in rows if r.pr_id == pr.id)
            assert row.quote_count == 1
            assert row.effective_quote_count == 0
            assert row.month_complete_rate is None
            assert row.overdue_rate is None
        finally:
            tenant_id_ctx.reset(tok)

    async def test_invalid_month_raises(self, session: AsyncSession, tenant_a: Any) -> None:
        from app.modules.report.exceptions import ReportInvalidTimeRangeError

        with pytest.raises(ReportInvalidTimeRangeError):
            await WorkProgressService(session).get_for_month(tenant_a.id, "2026-13")


class TestTargetPlanning:
    async def test_set_and_list_with_status(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            svc = TargetPlanningService(session)
            # 设目标 3，实际 2 篇 → 未达标 gap=-1
            await svc.set_target(
                TargetCreate(
                    pr_id=pr.id,
                    style_id=style.id,
                    period_month="2026-05",
                    min_target=3,
                ),
                pr,
            )
            for _ in range(2):
                await promotion_factory.promotion(
                    style=style,
                    blogger=blogger,
                    pr=pr,
                    cooperation_date=date(2026, 5, 8),
                )
            await session.commit()

            rows = await svc.list_with_actuals(tenant_a.id, "2026-05")
            assert len(rows) == 1
            assert rows[0].min_target == 3
            assert rows[0].actual_count == 2
            assert rows[0].status == "未达标"
            assert rows[0].gap == -1
        finally:
            tenant_id_ctx.reset(tok)

    async def test_upsert_overwrites_target(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            svc = TargetPlanningService(session)
            base = TargetCreate(
                pr_id=pr.id,
                style_id=style.id,
                period_month="2026-05",
                min_target=3,
            )
            await svc.set_target(base, pr)
            await svc.set_target(base.model_copy(update={"min_target": 8}), pr)
            await session.commit()
            rows = await svc.list_with_actuals(tenant_a.id, "2026-05")
            assert len(rows) == 1
            assert rows[0].min_target == 8
        finally:
            tenant_id_ctx.reset(tok)


class TestStoreDaily:
    async def test_aggregate_with_manual_join(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        operations_role: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            ops = await factory.user(tenant_a, roles=[operations_role])
            style = await product_factory.style()
            pp = await _platform_product(session, tenant_a, style)
            day = date(2026, 5, 15)
            # 同日两商品 → SUM 聚合
            await _qianniu(session, tenant_a, pp, day, pay="600.00", visitors=60, orders=6)
            pp2 = await _platform_product(session, tenant_a, style)
            await _qianniu(session, tenant_a, pp2, day, pay="400.00", visitors=40, orders=4)
            svc = StoreDailyService(session)
            # 手动 upsert 广告花费
            await svc.upsert_manual(
                tenant_a.id,
                day,
                StoreDailyManualUpdate(ad_spend_total=Decimal("150.00")),
                ops,
            )
            await session.commit()

            rows = await svc.get_dashboard(tenant_a.id, (day, day))
            assert len(rows) == 1
            assert rows[0].visitors == 100
            assert rows[0].pay_amount == Decimal("1000.00")
            assert rows[0].pay_orders == 10
            assert rows[0].ad_spend_total == Decimal("150.00")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_manual_upsert_overwrites(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        operations_role: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            ops = await factory.user(tenant_a, roles=[operations_role])
            day = date(2026, 5, 16)
            svc = StoreDailyService(session)
            await svc.upsert_manual(
                tenant_a.id,
                day,
                StoreDailyManualUpdate(zhitongche_spend=Decimal("50.00")),
                ops,
            )
            row = await svc.upsert_manual(
                tenant_a.id,
                day,
                StoreDailyManualUpdate(zhitongche_spend=Decimal("80.00")),
                ops,
            )
            await session.commit()
            assert row.zhitongche_spend == Decimal("80.00")
        finally:
            tenant_id_ctx.reset(tok)


class TestProduction:
    async def test_cross_table_and_week_over_week(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            goods = await _goods(session, tenant_a, style)
            pp = await _platform_product(session, tenant_a, style, goods=goods)
            ad_pp = await _platform_product(
                session, tenant_a, style, platform="万相台", goods=goods
            )
            cur = date(2026, 5, 20)
            prev = date(2026, 5, 19)  # 上一周期（跨度 0 天 → 前一天）
            # 本期：支付 1000 退款 100 + 广告 200 + promotion 500
            await _qianniu(
                session,
                tenant_a,
                pp,
                cur,
                pay="1000.00",
                extra={"refund_amount": "100.00", "add_cart_count": 50},
            )
            await _ad(session, tenant_a, ad_pp, cur, cost="200.00")
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                cooperation_date=cur,
                quote_amount=Decimal("500.00"),
                publish_status="已发布",
            )
            # 未发布和已停用推广均不得计入投产成本。
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                cooperation_date=cur,
                quote_amount=Decimal("900.00"),
                publish_status="未发布",
            )
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                pr=pr,
                cooperation_date=cur,
                quote_amount=Decimal("800.00"),
                publish_status="已发布",
                is_active=False,
            )
            # 上期：支付 800
            await _qianniu(session, tenant_a, pp, prev, pay="800.00")
            await session.commit()

            report = await ProductionService(session).get_report(tenant_a.id, (cur, cur))
            assert len(report.items) == 1
            row = report.items[0]
            assert row.pay_amount == Decimal("1000.00")
            assert row.refund_amount == Decimal("100.00")
            assert row.confirmed_amount == Decimal("900.00")
            assert row.total_spend == Decimal("700.00")  # 200 ad + 500 promo
            # net_roi = 900 / 700
            assert row.net_roi == Decimal("1.2857")
            assert row.return_rate == Decimal("0.1000")
            # 上一周期独立计算
            assert report.previous is not None
            prev_rows = [p for p in report.previous if p.goods_id == goods.id]
            assert prev_rows and prev_rows[0].pay_amount == Decimal("800.00")

            trend = await ProductionService(session).get_trend(
                tenant_a.id, goods.id, (cur, cur), granularity="day"
            )
            assert len(trend.points) == 1
            point = trend.points[0]
            assert point.date == cur
            assert point.pay_amount == Decimal("1000.00")
            assert point.refund_amount == Decimal("100.00")
            assert point.confirmed_amount == Decimal("900.00")
            assert point.promo_cost == Decimal("500.00")
            assert point.ad_spend == Decimal("200.00")
            assert point.total_spend == Decimal("700.00")
            assert point.net_roi == Decimal("1.2857")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_trend_zero_spend_returns_null_roi(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            goods = await _goods(session, tenant_a, style)
            pp = await _platform_product(session, tenant_a, style, goods=goods)
            day = date(2026, 5, 21)
            await _qianniu(
                session,
                tenant_a,
                pp,
                day,
                pay="300.00",
                extra={"refund_amount": "20.00"},
            )
            await session.commit()

            trend = await ProductionService(session).get_trend(
                tenant_a.id, goods.id, (day, day), granularity="day"
            )
            assert len(trend.points) == 1
            point = trend.points[0]
            assert point.confirmed_amount == Decimal("280.00")
            assert point.total_spend == Decimal("0")
            assert point.net_roi is None
        finally:
            tenant_id_ctx.reset(tok)

    async def test_multi_mapping_deduplicates_and_brushing_toggle(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            other_style = await product_factory.style()
            style.qianniu_product_id = "LEGACY-ID"
            goods = await _goods(session, tenant_a, style)
            other_goods = await _goods(session, tenant_a, other_style)
            pp1 = await _platform_product(session, tenant_a, style, goods=goods)
            await _platform_product(session, tenant_a, style, goods=goods)
            conflicting_q = PlatformProduct(
                tenant_id=tenant_a.id,
                platform="千牛",
                platform_id="LEGACY-ID",
                style_id=other_style.id,
                goods_main_id=other_goods.id,
            )
            ad_pp = await _platform_product(
                session, tenant_a, style, platform="万相台", goods=goods
            )
            cross_platform_ad = PlatformProduct(
                tenant_id=tenant_a.id,
                platform="千牛",
                platform_id="SHARED-AD-ID",
                style_id=other_style.id,
                goods_main_id=other_goods.id,
            )
            session.add_all([conflicting_q, cross_platform_ad])
            await session.flush()
            day = date(2026, 5, 22)
            session.add(
                QianniuDaily(
                    tenant_id=tenant_a.id,
                    platform_product_id=pp1.id,
                    platform_id_snapshot="LEGACY-ID",
                    date=day,
                    visitors=10,
                    pay_amount=Decimal("100.00"),
                    pay_orders=1,
                    extra={"refund_amount": "0", "add_cart_count": 2},
                )
            )
            session.add(
                AdDaily(
                    tenant_id=tenant_a.id,
                    platform_product_id=ad_pp.id,
                    platform_id_snapshot="SHARED-AD-ID",
                    date=day,
                    cost=Decimal("20.00"),
                    extra={"点击量": "3"},
                )
            )
            session.add(
                OrderAdjustment(
                    tenant_id=tenant_a.id,
                    order_type="刷单",
                    style_id=style.id,
                    amount=Decimal("30.00"),
                    order_date=day,
                    exclude_from_roi=True,
                    status="待付款",
                )
            )
            # legacy snapshot 命中的款式落在两个不同商品时不得归入任一商品，
            # 避免同一条日报被两个商品各算一次。
            legacy_a = await product_factory.style()
            legacy_b = await product_factory.style()
            legacy_a.qianniu_product_id = "AMBIGUOUS-LEGACY-ID"
            legacy_b.qianniu_product_id = "AMBIGUOUS-LEGACY-ID"
            await _goods(session, tenant_a, legacy_a)
            await _goods(session, tenant_a, legacy_b)
            session.add(
                QianniuDaily(
                    tenant_id=tenant_a.id,
                    platform_product_id=None,
                    platform_id_snapshot="AMBIGUOUS-LEGACY-ID",
                    date=day,
                    visitors=99,
                    pay_amount=Decimal("999.00"),
                    pay_orders=9,
                    extra={"add_cart_count": 99},
                )
            )
            await session.commit()

            service = ProductionService(session)
            included = await service.get_report(tenant_a.id, (day, day), exclude_brushing=False)
            excluded = await service.get_report(tenant_a.id, (day, day), exclude_brushing=True)
            extra_rows = await ProductionRepository(session).fetch_extra_by_goods(
                tenant_id=tenant_a.id, date_from=day, date_to=day
            )
            assert len(extra_rows) == 2
            assert all(row["goods_id"] == goods.id for row in extra_rows)
            assert len(included.items) == 1
            assert len(excluded.items) == 1
            assert included.items[0].goods_id == goods.id
            assert included.items[0].pay_amount == Decimal("100.00")
            assert included.items[0].ad_spend == Decimal("20.00")
            assert excluded.items[0].pay_amount == Decimal("70.00")

            trend_included = await service.get_trend(
                tenant_a.id, goods.id, (day, day), exclude_brushing=False
            )
            trend_excluded = await service.get_trend(
                tenant_a.id, goods.id, (day, day), exclude_brushing=True
            )
            assert trend_included.points[0].pay_amount == Decimal("100.00")
            assert trend_included.points[0].ad_spend == Decimal("20.00")
            assert trend_excluded.points[0].pay_amount == Decimal("70.00")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_suit_merges_into_one_row_and_sums_member_costs(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """套装：两款共用一条链接 → 报表一行，销售额算一次，成员推广费求和。

        这是按商品聚合的核心价值。按款式聚合时同一条链接的销售额会被两个款式
        各算一遍（总额翻倍），而套装本来只卖出了那么多。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            blogger = await blogger_factory.blogger()
            top = await product_factory.style(style_code="SUIT_TOP", style_name="上衣")
            skirt = await product_factory.style(style_code="SUIT_SKIRT", style_name="半裙")
            suit = await _goods(session, tenant_a, top, skirt, code="SUIT-1", is_suit=True)
            pp = await _platform_product(session, tenant_a, top, goods=suit)
            day = date(2026, 6, 10)
            await _qianniu(
                session,
                tenant_a,
                pp,
                day,
                pay="2000.00",
                extra={"refund_amount": "100.00", "add_cart_count": 30},
            )
            # 两个成员款式各有一笔已发布推广，套装的站外花费应当是两者之和。
            for style in (top, skirt):
                await promotion_factory.promotion(
                    style=style,
                    blogger=blogger,
                    pr=pr,
                    cooperation_date=day,
                    quote_amount=Decimal("300.00"),
                    publish_status="已发布",
                )
            await session.commit()

            report = await ProductionService(session).get_report(tenant_a.id, (day, day))
            rows = [r for r in report.items if r.goods_id == suit.id]
            assert len(rows) == 1, "套装必须合并成一行"
            row = rows[0]
            assert row.is_suit is True
            assert sorted(row.style_codes) == ["SUIT_SKIRT", "SUIT_TOP"]
            assert row.goods_code == "SUIT-1"
            # 销售额只算一次，不因为两个成员款式而翻倍
            assert row.pay_amount == Decimal("2000.00")
            assert row.refund_amount == Decimal("100.00")
            assert row.add_cart_count == 30
            # 站外推广费 = 两个成员款式之和
            assert row.promo_cost == Decimal("600.00")

            trend = await ProductionService(session).get_trend(tenant_a.id, suit.id, (day, day))
            assert len(trend.points) == 1
            assert trend.points[0].pay_amount == Decimal("2000.00")
            assert trend.points[0].promo_cost == Decimal("600.00")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_style_in_both_solo_and_suit_does_not_double_count_promo(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """款式既单卖又进套装时，同一笔推广费只能算给一个商品。

        生产上 260419（木耳边打底衫）就是这个形态：它有自己的独立销售链接，
        同时又和 260415 组成套装。推广费挂在款式上，如果两个商品都算就会让
        全店总花费翻倍、投产比虚低。取主商品（非套装优先）保证总额不重复。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            blogger = await blogger_factory.blogger()
            shared = await product_factory.style(style_code="DUAL_SHARED", style_name="打底衫")
            partner = await product_factory.style(style_code="DUAL_PARTNER", style_name="马甲")
            solo = await _goods(session, tenant_a, shared, code="DUAL-SOLO")
            suit = await _goods(session, tenant_a, shared, partner, code="DUAL-SUIT", is_suit=True)
            solo_pp = await _platform_product(session, tenant_a, shared, goods=solo)
            suit_pp = await _platform_product(session, tenant_a, shared, goods=suit)
            day = date(2026, 6, 12)
            await _qianniu(session, tenant_a, solo_pp, day, pay="400.00")
            await _qianniu(session, tenant_a, suit_pp, day, pay="1000.00")
            await promotion_factory.promotion(
                style=shared,
                blogger=blogger,
                pr=pr,
                cooperation_date=day,
                quote_amount=Decimal("250.00"),
                publish_status="已发布",
            )
            await session.commit()

            report = await ProductionService(session).get_report(tenant_a.id, (day, day))
            by_code = {r.goods_code: r for r in report.items}
            assert "DUAL-SOLO" in by_code and "DUAL-SUIT" in by_code
            # 销售额各归各的链接
            assert by_code["DUAL-SOLO"].pay_amount == Decimal("400.00")
            assert by_code["DUAL-SUIT"].pay_amount == Decimal("1000.00")
            # 推广费只算一次，落在非套装的那个商品上
            assert by_code["DUAL-SOLO"].promo_cost == Decimal("250.00")
            assert by_code["DUAL-SUIT"].promo_cost == Decimal("0")
            total_promo = sum(r.promo_cost for r in report.items)
            assert total_promo == Decimal("250.00"), "全店推广费不能因为一款两归属而翻倍"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_suit_sharing_qianniu_id_without_link_still_merges(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """兜底路径也要支持套装：两款手填同一个千牛ID、没建链接，仍合并成一行。

        旧口径的护栏是「同一千牛ID 只能绑一个款式」，这种数据会被整条丢掉；
        现在放宽成「只能落在一个商品」，套装能正常统计。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            a = await product_factory.style(style_code="SQN_A", style_name="外套")
            b = await product_factory.style(style_code="SQN_B", style_name="马甲")
            a.qianniu_product_id = "SUIT-QN-ID"
            b.qianniu_product_id = "SUIT-QN-ID"
            suit = await _goods(session, tenant_a, a, b, code="SUIT-QN", is_suit=True)
            day = date(2026, 6, 11)
            session.add(
                QianniuDaily(
                    tenant_id=tenant_a.id,
                    platform_product_id=None,
                    platform_id_snapshot="SUIT-QN-ID",
                    date=day,
                    visitors=50,
                    pay_amount=Decimal("1500.00"),
                    pay_orders=5,
                    extra={"add_cart_count": 12},
                )
            )
            await session.commit()

            report = await ProductionService(session).get_report(tenant_a.id, (day, day))
            rows = [r for r in report.items if r.goods_id == suit.id]
            assert len(rows) == 1
            assert rows[0].pay_amount == Decimal("1500.00")
            # extra 只累加一次，不因为两个成员款式而翻倍
            assert rows[0].add_cart_count == 12
            assert rows[0].extra.get("add_cart_count") == "12"
        finally:
            tenant_id_ctx.reset(tok)


class TestRls:
    async def test_work_progress_tenant_isolation(
        self,
        session: AsyncSession,
        tenant_a: Any,
        tenant_b: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_b.id)
        try:
            pr_b = await factory.user(tenant_b, roles=[pr_role])
            style_b = await product_factory.style(tenant=tenant_b)
            blogger_b = await blogger_factory.blogger(tenant=tenant_b)
            await promotion_factory.promotion(
                style=style_b,
                blogger=blogger_b,
                pr=pr_b,
                tenant=tenant_b,
                cooperation_date=date(2026, 5, 10),
            )
            await session.commit()
            # 查 tenant_a 的工作进度 → 不含 tenant_b 数据
            rows_a = await WorkProgressService(session).get_for_month(tenant_a.id, "2026-05")
            assert all(r.pr_id != pr_b.id for r in rows_a)
        finally:
            tenant_id_ctx.reset(tok)
