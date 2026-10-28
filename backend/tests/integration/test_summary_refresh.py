"""汇总表刷新：写进去的数字必须和实时聚合一字不差。

069移除无人读取的周/月缓存；三张日汇总继续支撑当前读取路径。

覆盖四件事：

1. **刷新结果 == 实时聚合**（3 张表各一条）
2. **幂等**：同一区间刷两次，行数与数字都不变
3. **陈旧行清理**：源数据删掉后，汇总表对应的行要跟着消失（纯 upsert 会漏这个）
4. **增量边界**：只刷 3 月不能动 4 月已有的汇总行

第 3 条是最容易被漏掉的：只做 upsert 的实现跑完测试一样绿，上线后千牛日报一改
货号，旧商品的汇总行就永远留在那里，区间求和偏高且无人察觉。

日表仍保留原来的完整周/月刷新窗口；第一次刷新也不能漏掉窗口外的月初。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import AdDaily, QianniuDaily
from app.modules.finance.order_adjustment_models import OrderAdjustment
from app.modules.metric_config.service import PlatformMetricSettingService
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.advanced_repository import (
    ProductionRepository,
    StoreDailyRepository,
    WorkProgressRepository,
)
from app.modules.report.summary_models import (
    SHOP_METRICS,
    ProductRoiSummary,
    PrWorkProgressSummary,
    ShopDailySummary,
)
from app.modules.report.summary_refresh_service import SummaryRefreshService
from app.modules.urge.service import UrgeService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 2026-03-02 周一，03-08 周日 → 03-09 进下一个 ISO 周
D1 = date(2026, 3, 2)
D2 = date(2026, 3, 5)
D3 = date(2026, 3, 9)
MAR_LO, MAR_HI = date(2026, 3, 1), date(2026, 3, 31)
APR_DAY = date(2026, 4, 1)
FULL_LO, FULL_HI = date(2026, 3, 1), date(2026, 4, 30)


async def _goods(session: AsyncSession, tenant: Any, *styles: Any, code: str) -> GoodsMain:
    goods = GoodsMain(
        tenant_id=tenant.id, goods_code=code, goods_title=f"{code} 商品", is_suit=False
    )
    session.add(goods)
    await session.flush()
    for idx, style in enumerate(styles):
        session.add(
            GoodsStyleItem(
                tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id, sort_order=idx
            )
        )
    await session.flush()
    return goods


async def _pp(
    session: AsyncSession, tenant: Any, style: Any, goods: GoodsMain, *, platform: str = "千牛"
) -> PlatformProduct:
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


async def _seed(
    session: AsyncSession,
    tenant: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> dict[str, Any]:
    style = await product_factory.style(style_code=f"RS{uuid4().hex[:6]}")
    goods = await _goods(session, tenant, style, code=f"G_RS_{uuid4().hex[:6]}")
    qn = await _pp(session, tenant, style, goods)
    ad = await _pp(session, tenant, style, goods, platform="万相台")

    for day, pay, refund in ((D1, "1000.00", "50.00"), (D3, "800.00", "20.00")):
        session.add(
            QianniuDaily(
                tenant_id=tenant.id,
                platform_product_id=qn.id,
                platform_id_snapshot=qn.platform_id,
                date=day,
                visitors=120,
                pay_amount=Decimal(pay),
                pay_orders=8,
                refund_amount=Decimal(refund),
                add_cart_count=3,
            )
        )
    # 4 月那天单独留着，用来验证「只刷 3 月不碰 4 月」
    session.add(
        QianniuDaily(
            tenant_id=tenant.id,
            platform_product_id=qn.id,
            platform_id_snapshot=qn.platform_id,
            date=APR_DAY,
            visitors=60,
            pay_amount=Decimal("600.00"),
            pay_orders=4,
            refund_amount=Decimal("0"),
        )
    )
    session.add(
        AdDaily(
            tenant_id=tenant.id,
            platform_product_id=ad.id,
            platform_id_snapshot=ad.platform_id,
            date=D1,
            cost=Decimal("150.00"),
            extra={},
        )
    )
    session.add(
        OrderAdjustment(
            tenant_id=tenant.id,
            order_type="刷单",
            style_id=style.id,
            amount=Decimal("200.00"),
            order_date=D2,
            exclude_from_roi=True,
            status="待付款",
        )
    )
    blogger = await blogger_factory.blogger()
    await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        goods_main_id=goods.id,
        cooperation_date=D2,
        quote_amount=Decimal("450.00"),
        publish_status="已发布",
        like_count=1200,
    )
    await session.flush()
    return {"style": style, "goods": goods, "qn": qn}


class TestRefreshMatchesLiveAggregation:
    async def test_product_roi_rows_equal_live_trend(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """product_roi_summary 的每一行都等于 daily_trend_by_goods 的对应行。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory, blogger_factory, promotion_factory)
            await session.commit()

            await SummaryRefreshService(session).refresh(
                tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI
            )
            await session.commit()

            live = await ProductionRepository(session).daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=FULL_LO,
                date_to=FULL_HI,
                granularity="day",
                exclude_brushing=False,
            )
            stored = (
                (
                    await session.execute(
                        select(ProductRoiSummary).where(ProductRoiSummary.tenant_id == tenant_a.id)
                    )
                )
                .scalars()
                .all()
            )
            assert len(stored) == len(live) > 0
            by_key = {(s.goods_main_id, s.channel, s.stat_date): s for s in stored}
            for row in live:
                got = by_key[(row["goods_id"], row["channel"], row["date"])]
                for metric in (
                    "pay_amount",
                    "brushing_amount",
                    "refund_amount",
                    "promo_cost",
                    "ad_spend",
                ):
                    assert Decimal(getattr(got, metric)) == Decimal(row[metric]), (
                        f"{row['date']}.{metric}: 汇总 {getattr(got, metric)} "
                        f"!= 实时 {row[metric]}"
                    )
            # 场景确实产生了刷单与推广，否则上面在比一堆 0
            assert any(s.brushing_amount != Decimal("0") for s in stored)
            assert any(s.promo_cost != Decimal("0") for s in stored)
            assert any(s.ad_spend != Decimal("0") for s in stored)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_pr_progress_interval_sum_equals_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """逐日存的计数 SUM 回去，必须等于实时的区间聚合。

        这是「逐日刷新 + 读取时求和」这个设计的正确性证明：10 个落盘计数都可加。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory, blogger_factory, promotion_factory)
            await session.commit()

            await SummaryRefreshService(session).refresh(
                tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI
            )
            await session.commit()

            live = await WorkProgressRepository(session).aggregate_by_pr(
                tenant_id=tenant_a.id,
                date_from=FULL_LO,
                date_to=FULL_HI,
                today=FULL_HI,
                thresholds=await UrgeService(session).get_urge_thresholds(tenant_a.id),
                metric_settings=await PlatformMetricSettingService(session).get_settings(
                    tenant_a.id
                ),
                by_goods=False,
            )
            assert live, "场景没有推广单，PR 进度对比是空跑"

            cols = (
                "quote_count",
                "publish_count",
                "info_complete_count",
                "cancel_count",
                "recall_due_count",
                "recall_success_count",
                "effective_quote_count",
                "hit_count",
                "like_count",
                "cost",
            )
            agg = (
                await session.execute(
                    select(
                        PrWorkProgressSummary.pr_id,
                        *[
                            func.coalesce(func.sum(getattr(PrWorkProgressSummary, c)), 0)
                            for c in cols
                        ],
                    )
                    .where(PrWorkProgressSummary.tenant_id == tenant_a.id)
                    .group_by(PrWorkProgressSummary.pr_id)
                )
            ).all()
            summed = {r[0]: dict(zip(cols, r[1:], strict=True)) for r in agg}

            for row in live:
                got = summed[row["pr_id"]]
                for col in cols:
                    assert Decimal(got[col]) == Decimal(
                        row[col] or 0
                    ), f"pr={row['pr_id']} {col}: 汇总合计 {got[col]} != 实时 {row[col]}"
            assert any(v["quote_count"] > 0 for v in summed.values())
        finally:
            tenant_id_ctx.reset(tok)

    async def test_shop_daily_equals_live(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """店铺日汇总 == 实时聚合。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory, blogger_factory, promotion_factory)
            await session.commit()

            await SummaryRefreshService(session).refresh(
                tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI
            )
            await session.commit()

            live = await StoreDailyRepository(session).aggregate(
                tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI
            )
            stored = (
                (
                    await session.execute(
                        select(ShopDailySummary)
                        .where(ShopDailySummary.tenant_id == tenant_a.id)
                        .order_by(ShopDailySummary.stat_date)
                    )
                )
                .scalars()
                .all()
            )
            assert len(stored) == len(live) > 0
            for got, row in zip(stored, live, strict=True):
                assert got.stat_date == row["date"]
                assert got.visitors == (row["visitors"] or 0)
                assert Decimal(got.pay_amount) == Decimal(row["pay_amount"] or 0)
                assert got.pay_orders == (row["pay_orders"] or 0)

        finally:
            tenant_id_ctx.reset(tok)


class TestRefreshIsRepeatableAndIncremental:
    async def test_second_refresh_changes_nothing(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """同一区间刷两次，行数与数字都不变。

        刷新是每小时跑的定时任务，不幂等就会累积重复行或把数字翻倍。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory, blogger_factory, promotion_factory)
            await session.commit()

            svc = SummaryRefreshService(session)
            first = await svc.refresh(tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI)
            await session.commit()
            snapshot = await self._dump(session, tenant_a.id)

            second = await svc.refresh(tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI)
            await session.commit()

            assert first == second
            assert await self._dump(session, tenant_a.id) == snapshot
        finally:
            tenant_id_ctx.reset(tok)

    async def test_stale_rows_are_removed_when_source_shrinks(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """源数据删掉之后，对应的汇总行必须消失。

        这条是为纯 upsert 实现立的：那种实现跑完全绿，但上线后千牛日报一改货号，
        旧商品的汇总行会永远留着，区间求和偏高且不报错。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()

            svc = SummaryRefreshService(session)
            await svc.refresh(tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI)
            await session.commit()
            before = await session.scalar(
                select(func.count())
                .select_from(ProductRoiSummary)
                .where(ProductRoiSummary.tenant_id == tenant_a.id)
            )
            assert before and before >= 2

            # 删掉 3 月那两条千牛日报（刷单与推广留着，所以不是整商品消失）
            await session.execute(
                delete(QianniuDaily).where(
                    QianniuDaily.tenant_id == tenant_a.id,
                    QianniuDaily.date.in_([D1, D3]),
                )
            )
            await session.commit()

            await svc.refresh(tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI)
            await session.commit()

            live = await ProductionRepository(session).daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=FULL_LO,
                date_to=FULL_HI,
                granularity="day",
                exclude_brushing=False,
            )
            after = await session.scalar(
                select(func.count())
                .select_from(ProductRoiSummary)
                .where(ProductRoiSummary.tenant_id == tenant_a.id)
            )
            assert after == len(live) < before, "陈旧行没有被清掉"

            # 被删掉那两天不该再有行
            leftover = await session.scalar(
                select(func.count())
                .select_from(ProductRoiSummary)
                .where(
                    ProductRoiSummary.tenant_id == tenant_a.id,
                    ProductRoiSummary.stat_date.in_([D1, D3]),
                    ProductRoiSummary.pay_amount != Decimal("0"),
                )
            )
            assert leftover == 0

            # 店铺日汇总同样要跟着缩。D1 还有万相台花费，按日期并集（062）那天仍有一行，
            # 但千牛的数字必须清掉；D3 没有其他来源，整行消失
            shop_left = (
                await session.execute(
                    select(ShopDailySummary.stat_date, ShopDailySummary.pay_amount)
                    .where(
                        ShopDailySummary.tenant_id == tenant_a.id,
                        ShopDailySummary.stat_date.in_([D1, D3]),
                    )
                    .order_by(ShopDailySummary.stat_date)
                )
            ).all()
            assert [(d, Decimal(p)) for d, p in shop_left] == [(D1, Decimal("0"))]
            seeded_goods = seeded["goods"]
            assert seeded_goods is not None
        finally:
            tenant_id_ctx.reset(tok)

    async def test_refreshing_march_leaves_april_untouched(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """只刷 3 月不能动 4 月的行 —— 这是增量刷新，不是重建。

        ``_replace`` 的 DELETE 少写一个日期条件就会变成 truncate，
        表现是「每小时刷新一次，历史汇总被清光，只剩最近 31 天」。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory, blogger_factory, promotion_factory)
            await session.commit()

            svc = SummaryRefreshService(session)
            await svc.refresh(tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI)
            await session.commit()

            april_before = (
                (
                    await session.execute(
                        select(ProductRoiSummary).where(
                            ProductRoiSummary.tenant_id == tenant_a.id,
                            ProductRoiSummary.stat_date == APR_DAY,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(april_before) == 1
            april_pay = april_before[0].pay_amount

            # 只刷 3 月
            await svc.refresh(tenant_id=tenant_a.id, date_from=MAR_LO, date_to=MAR_HI)
            await session.commit()

            april_after = (
                (
                    await session.execute(
                        select(ProductRoiSummary).where(
                            ProductRoiSummary.tenant_id == tenant_a.id,
                            ProductRoiSummary.stat_date == APR_DAY,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(april_after) == 1
            assert april_after[0].pay_amount == april_pay
            # 4 月的店铺日汇总也要还在
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(ShopDailySummary)
                    .where(
                        ShopDailySummary.tenant_id == tenant_a.id,
                        ShopDailySummary.stat_date == APR_DAY,
                    )
                )
                == 1
            )
        finally:
            tenant_id_ctx.reset(tok)

    async def test_half_month_refresh_keeps_full_month_daily_span(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """只刷半个月仍保留既有整月日表窗口，周/月报表继续由日行分桶。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory, blogger_factory, promotion_factory)
            await session.commit()

            svc = SummaryRefreshService(session)
            await svc.refresh(tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI)
            await session.commit()
            full_march = await session.scalar(
                select(func.sum(ShopDailySummary.pay_amount)).where(
                    ShopDailySummary.tenant_id == tenant_a.id,
                    ShopDailySummary.stat_date >= MAR_LO,
                    ShopDailySummary.stat_date <= MAR_HI,
                )
            )
            assert full_march and full_march > Decimal("0")

            # 只刷 3/8~3/12：D1(3/2) 在区间外，但它和 D3(3/9) 同属 3 月
            await svc.refresh(
                tenant_id=tenant_a.id, date_from=date(2026, 3, 8), date_to=date(2026, 3, 12)
            )
            await session.commit()

            after = await session.scalar(
                select(func.sum(ShopDailySummary.pay_amount)).where(
                    ShopDailySummary.tenant_id == tenant_a.id,
                    ShopDailySummary.stat_date >= MAR_LO,
                    ShopDailySummary.stat_date <= MAR_HI,
                )
            )
            assert after == full_march, "日汇总的整月窗口被缩成半个月了"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_window_starting_mid_month_still_counts_month_start(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """刷新窗口从月中开始、且月初那几天**从没刷过**，月汇总仍须含月初的数据。

        这是生产定时任务的真实形态：窗口是「今天往前 31 天」，第一次刷新是 09-02 ~
        10-02，9 月这一行要从 09-01 起的日汇总加起来 —— 但 09-01 不在窗口里，
        日汇总从没写过那天。

        上面那条「半个月刷新」的测试**发现不了**这个问题：它先做了一次全量刷新，
        月初的日汇总已经在表里了。这里刻意不做任何前置刷新。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code=f"MS{uuid4().hex[:6]}")
            goods = await _goods(session, tenant_a, style, code=f"G_MS_{uuid4().hex[:6]}")
            qn = await _pp(session, tenant_a, style, goods)
            for day, pay in ((date(2026, 9, 1), "300.00"), (date(2026, 9, 15), "500.00")):
                session.add(
                    QianniuDaily(
                        tenant_id=tenant_a.id,
                        platform_product_id=qn.id,
                        platform_id_snapshot=qn.platform_id,
                        date=day,
                        visitors=10,
                        pay_amount=Decimal(pay),
                        pay_orders=1,
                        extra={},
                    )
                )
            await session.commit()

            # 与生产第一次刷新同一个窗口，且之前没有任何刷新
            await SummaryRefreshService(session).refresh(
                tenant_id=tenant_a.id, date_from=date(2026, 9, 2), date_to=date(2026, 10, 2)
            )
            await session.commit()

            september = await session.scalar(
                select(func.sum(ShopDailySummary.pay_amount)).where(
                    ShopDailySummary.tenant_id == tenant_a.id,
                    ShopDailySummary.stat_date >= date(2026, 9, 1),
                    ShopDailySummary.stat_date < date(2026, 10, 1),
                )
            )
            assert september == Decimal(
                "800.00"
            ), f"9 月汇总 {september} 缺了 09-01 的 300 —— 窗口外的月初日汇总没被刷到"
            # 09-01 所在的周（08-31 起）同样要完整
            week = await session.scalar(
                select(func.sum(ShopDailySummary.pay_amount)).where(
                    ShopDailySummary.tenant_id == tenant_a.id,
                    ShopDailySummary.stat_date >= date(2026, 8, 31),
                    ShopDailySummary.stat_date < date(2026, 9, 7),
                )
            )
            assert week == Decimal("300.00")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_first_half_month_refresh_daily_span_preserves_all_metrics(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """首次半月刷新也保留窗口外月初以及九项日指标的各自来源。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code=f"HM{uuid4().hex[:6]}")
            goods = await _goods(session, tenant_a, style, code=f"G_HM_{uuid4().hex[:6]}")

            async def link(platform: str, channel: str) -> PlatformProduct:
                pp = PlatformProduct(
                    tenant_id=tenant_a.id,
                    platform=platform,
                    platform_id=f"P{uuid4().hex[:8]}",
                    style_id=style.id,
                    goods_main_id=goods.id,
                    channel=channel,
                )
                session.add(pp)
                await session.flush()
                return pp

            qn_norm, qn_live = await link("千牛", "普通"), await link("千牛", "直播")
            ad_norm, ad_live = await link("万相台", "普通"), await link("万相台", "直播")
            for pp, day, pay, refund in (
                (qn_norm, date(2026, 9, 1), "300.00", "30.00"),
                (qn_live, date(2026, 9, 3), "200.00", "20.00"),
            ):
                session.add(
                    QianniuDaily(
                        tenant_id=tenant_a.id,
                        platform_product_id=pp.id,
                        platform_id_snapshot=pp.platform_id,
                        date=day,
                        visitors=10,
                        pay_amount=Decimal(pay),
                        pay_orders=1,
                        refund_amount=Decimal(refund),
                    )
                )
            for pp, cost in ((ad_norm, "50.00"), (ad_live, "20.00")):
                session.add(
                    AdDaily(
                        tenant_id=tenant_a.id,
                        platform_product_id=pp.id,
                        platform_id_snapshot=pp.platform_id,
                        date=date(2026, 9, 4),
                        cost=Decimal(cost),
                        extra={},
                    )
                )
            session.add(
                OrderAdjustment(
                    tenant_id=tenant_a.id,
                    order_type="刷单",
                    style_id=style.id,
                    amount=Decimal("100.00"),
                    order_date=date(2026, 9, 6),
                    exclude_from_roi=True,
                    status="待付款",
                )
            )
            await promotion_factory.promotion(
                style=style,
                blogger=await blogger_factory.blogger(),
                cooperation_date=date(2026, 9, 5),
                publish_status="已发布",
                quote_amount=Decimal("400.00"),
            )
            await session.commit()

            await SummaryRefreshService(session).refresh(
                tenant_id=tenant_a.id, date_from=date(2026, 9, 2), date_to=date(2026, 9, 16)
            )
            await session.commit()

            days = (
                (
                    await session.execute(
                        select(ShopDailySummary).where(ShopDailySummary.tenant_id == tenant_a.id)
                    )
                )
                .scalars()
                .all()
            )

            def day_sum(lo: date, hi: date, metric: str) -> Decimal:
                return sum(
                    (Decimal(getattr(d, metric)) for d in days if lo <= d.stat_date < hi),
                    Decimal("0"),
                )

            expected = {
                "visitors": 20,
                "pay_amount": 500,
                "pay_orders": 2,
                "refund_amount": 50,
                "brushing_amount": 100,
                "ad_spend": 70,
                "promo_cost": 400,
                "live_pay_amount": 200,
                "live_ad_spend": 20,
            }
            assert set(expected) == set(SHOP_METRICS)
            for metric, total in expected.items():
                assert day_sum(date(2026, 9, 1), date(2026, 10, 1), metric) == total, metric
                assert day_sum(date(2026, 8, 31), date(2026, 9, 7), metric) == total, metric
        finally:
            tenant_id_ctx.reset(tok)

    @staticmethod
    async def _dump(session: AsyncSession, tenant_id: Any) -> dict[str, list[Any]]:
        """抓一份可比对的快照（排除 id / 时间戳这些每次都变的列）。"""
        out: dict[str, list[Any]] = {}
        out["roi"] = sorted(
            (
                (r.goods_main_id, r.stat_date, r.pay_amount, r.brushing_amount, r.ad_spend)
                for r in (
                    await session.execute(
                        select(ProductRoiSummary).where(ProductRoiSummary.tenant_id == tenant_id)
                    )
                )
                .scalars()
                .all()
            ),
            key=lambda t: (str(t[0]), t[1]),
        )
        out["pr"] = sorted(
            (
                (str(r.pr_id), r.stat_date, r.quote_count, r.like_count, r.cost)
                for r in (
                    await session.execute(
                        select(PrWorkProgressSummary).where(
                            PrWorkProgressSummary.tenant_id == tenant_id
                        )
                    )
                )
                .scalars()
                .all()
            ),
            key=lambda t: (t[0], t[1]),
        )
        for key, model, bucket in (("day", ShopDailySummary, "stat_date"),):
            out[key] = sorted(
                (getattr(r, bucket), r.visitors, r.pay_amount, r.pay_orders)
                for r in (await session.execute(select(model).where(model.tenant_id == tenant_id)))
                .scalars()
                .all()
            )
        return out
