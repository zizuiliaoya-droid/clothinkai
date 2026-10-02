"""汇总表刷新：写进去的数字必须和实时聚合一字不差。

本 PR 刻意**不切换读取路径** —— 先刷新 + 校验，确认汇总表的数字与实时查询逐分
相等，再在下个 PR 切读。这组测试就是那个「确认」。

覆盖四件事：

1. **刷新结果 == 实时聚合**（5 张表各一条）
2. **幂等**：同一区间刷两次，行数与数字都不变
3. **陈旧行清理**：源数据删掉后，汇总表对应的行要跟着消失（纯 upsert 会漏这个）
4. **增量边界**：只刷 3 月不能动 4 月已有的汇总行

第 3 条是最容易被漏掉的：只做 upsert 的实现跑完测试一样绿，上线后千牛日报一改
货号，旧商品的汇总行就永远留在那里，区间求和偏高且无人察觉。

周/月表另有一条单独的坑：区间只覆盖半个月时，必须把桶补全成整月再算，
否则那一行只含半个月的数据。
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
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.advanced_repository import (
    ProductionRepository,
    StoreDailyRepository,
    WorkProgressRepository,
)
from app.modules.report.summary_models import (
    ProductRoiSummary,
    PrWorkProgressSummary,
    ShopDailySummary,
    ShopMonthSummary,
    ShopWeekSummary,
)
from app.modules.report.summary_refresh_service import SummaryRefreshService

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
                extra={"refund_amount": refund, "add_cart_count": "3"},
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
            extra={"refund_amount": "0"},
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
            by_key = {(s.goods_main_id, s.stat_date): s for s in stored}
            for row in live:
                got = by_key[(row["goods_id"], row["date"])]
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
                tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI, today=FULL_HI
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

    async def test_shop_daily_equals_live_and_week_month_fold(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """店铺日汇总 == 实时聚合；周/月汇总 == 日汇总的加总。"""
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

            # 周 / 月必须等于日的加总
            day_total = (
                Decimal(sum(s.pay_amount for s in stored)),
                sum(s.visitors for s in stored),
                sum(s.pay_orders for s in stored),
            )
            for model in (ShopWeekSummary, ShopMonthSummary):
                rows = (
                    (await session.execute(select(model).where(model.tenant_id == tenant_a.id)))
                    .scalars()
                    .all()
                )
                assert rows, f"{model.__tablename__} 没有数据"
                assert (
                    Decimal(sum(r.pay_amount for r in rows)),
                    sum(r.visitors for r in rows),
                    sum(r.pay_orders for r in rows),
                ) == day_total, f"{model.__tablename__} 合计与日表不一致"

            # 桶真的分开了：3 月两个 ISO 周 + 4 月一天 → 至少 3 个周桶、2 个月桶
            week_n = await session.scalar(
                select(func.count())
                .select_from(ShopWeekSummary)
                .where(ShopWeekSummary.tenant_id == tenant_a.id)
            )
            month_n = await session.scalar(
                select(func.count())
                .select_from(ShopMonthSummary)
                .where(ShopMonthSummary.tenant_id == tenant_a.id)
            )
            assert week_n >= 3
            assert month_n == 2
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

            # 店铺日汇总同样要跟着缩
            shop_left = await session.scalar(
                select(func.count())
                .select_from(ShopDailySummary)
                .where(
                    ShopDailySummary.tenant_id == tenant_a.id,
                    ShopDailySummary.stat_date.in_([D1, D3]),
                )
            )
            assert shop_left == 0
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

    async def test_half_month_refresh_still_writes_full_month_bucket(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """只刷半个月，月汇总那一行仍须是整月的数字。

        漏了「把区间补全成桶」这一步的话，月汇总会只含被刷到的那几天 ——
        数字偏小，而且因为行确实被更新了，看起来一切正常。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory, blogger_factory, promotion_factory)
            await session.commit()

            svc = SummaryRefreshService(session)
            await svc.refresh(tenant_id=tenant_a.id, date_from=FULL_LO, date_to=FULL_HI)
            await session.commit()
            full_march = await session.scalar(
                select(ShopMonthSummary.pay_amount).where(
                    ShopMonthSummary.tenant_id == tenant_a.id,
                    ShopMonthSummary.period_month == MAR_LO,
                )
            )
            assert full_march and full_march > Decimal("0")

            # 只刷 3/8~3/12：D1(3/2) 在区间外，但它和 D3(3/9) 同属 3 月
            await svc.refresh(
                tenant_id=tenant_a.id, date_from=date(2026, 3, 8), date_to=date(2026, 3, 12)
            )
            await session.commit()

            after = await session.scalar(
                select(ShopMonthSummary.pay_amount).where(
                    ShopMonthSummary.tenant_id == tenant_a.id,
                    ShopMonthSummary.period_month == MAR_LO,
                )
            )
            assert after == full_march, "月汇总被半个月的数据覆盖了"
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
        for key, model, bucket in (
            ("day", ShopDailySummary, "stat_date"),
            ("week", ShopWeekSummary, "week_start"),
            ("month", ShopMonthSummary, "period_month"),
        ):
            out[key] = sorted(
                (getattr(r, bucket), r.visitors, r.pay_amount, r.pay_orders)
                for r in (await session.execute(select(model).where(model.tenant_id == tenant_id)))
                .scalars()
                .all()
            )
        return out
