"""汇总表刷新源的口径等值性。

`product_roi_summary` 等 5 张中间汇总表要靠 `daily_trend_by_goods(goods_id=None)`
按 (商品, 日期桶) 预聚合出来。汇总表最大的风险不是性能也不是刷新调度，而是
**预聚合的口径和实时查询的口径悄悄分叉** —— 数字对不上，而且不报错。

这个项目已经为「同一个值两处定义」付过三次代价：催发阈值在推广模块旧常量文件和
scan_service 各写一份、点赞数在 like_count 和 source_extra['点赞数'] 各存一份、
推广列表的列白名单手写导致 5 个字段上线后一直是空的。所以刷新**复用**
`daily_trend_by_goods` 而不是另写一条 INSERT...SELECT，而这组测试就是复用是否
真的等价的证明。

钉死三件事：

1. 全商品按日逐商品合计 == `aggregate_by_goods` 的区间结果（汇总表读取的正确性）
2. 全商品路径筛出某商品 == 单商品路径（前端趋势图切到汇总表后数字不变）
3. week / month 桶的合计 == day 桶的合计（shop_week/month_summary 的正确性）

第 1 条有一个**刻意保留的差异**：`aggregate_by_goods` 带 HAVING（三项全 0 的商品
不出现在报表里），`daily_trend_by_goods` 没有。所以多出来的商品必须验证确实满足
「HAVING 为假」，而不是被静默忽略 —— 否则汇总表读取时会凭空多出几行空商品。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import AdDaily, QianniuDaily
from app.modules.finance.order_adjustment_models import OrderAdjustment
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.advanced_repository import ProductionRepository
from app.modules.report.summary_models import ProductRoiSummary
from app.modules.report.summary_refresh_service import SummaryRefreshService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 跨月且跨 ISO 周，让 week / month 桶真的分得开。
# 2026-03-02 是周一，03-08 是周日 → 和 03-09 分属两个 ISO 周。
D_MAR_02 = date(2026, 3, 2)
D_MAR_05 = date(2026, 3, 5)
D_MAR_09 = date(2026, 3, 9)
D_APR_01 = date(2026, 4, 1)
LO, HI = date(2026, 1, 1), date(2026, 12, 31)

# 比对的指标。add_cart_count 由 054 加进汇总表（投产报表「总加购数」靠它），
# net_roi 是比率不落盘，不参与等值比对。
_METRICS = ("pay_amount", "refund_amount", "add_cart_count", "promo_cost", "ad_spend")
# 062 加的两列。投产主表（aggregate_by_goods）要到按渠道改造时才出，所以只在
# 趋势自身的比对（全商品 ⇄ 单商品、粗桶 ⇄ 日桶）里用
_TREND_METRICS = (*_METRICS, "pay_orders", "ad_add_cart_count")


async def _goods(
    session: AsyncSession, tenant: Any, *styles: Any, code: str, is_suit: bool = False
) -> GoodsMain:
    goods = GoodsMain(
        tenant_id=tenant.id,
        goods_code=code,
        goods_title=f"{code} 商品",
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
    goods: GoodsMain,
    *,
    platform: str = "千牛",
    channel: str = "普通",
) -> PlatformProduct:
    pp = PlatformProduct(
        tenant_id=tenant.id,
        platform=platform,
        platform_id=f"P{uuid4().hex[:8]}",
        style_id=style.id,
        goods_main_id=goods.id,
        channel=channel,
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
    pay: str,
    refund: str = "0",
    pay_orders: int = 1,
) -> None:
    session.add(
        QianniuDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            visitors=10,
            pay_amount=Decimal(pay),
            pay_orders=pay_orders,
            refund_amount=Decimal(refund),
            add_cart_count=2,
        )
    )


async def _ad(
    session: AsyncSession,
    tenant: Any,
    pp: PlatformProduct,
    day: date,
    *,
    cost: str,
    add_cart: int | None = None,
) -> None:
    session.add(
        AdDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            cost=Decimal(cost),
            add_cart_count=add_cart,
            extra={},
        )
    )


async def _brushing(
    session: AsyncSession, tenant: Any, style: Any, day: date, *, amount: str
) -> None:
    session.add(
        OrderAdjustment(
            tenant_id=tenant.id,
            order_type="刷单",
            style_id=style.id,
            amount=Decimal(amount),
            order_date=day,
            exclude_from_roi=True,
            status="待付款",
        )
    )


def _fold_by_goods(
    rows: list[Any], metrics: tuple[str, ...] = _METRICS
) -> dict[UUID, dict[str, Decimal]]:
    """把 (商品, 渠道, 日期) 的逐日行按商品合并 —— 刷新任务写汇总表后读取时做的事。"""
    out: dict[UUID, dict[str, Decimal]] = defaultdict(lambda: dict.fromkeys(metrics, Decimal("0")))
    for r in rows:
        bucket = out[r["goods_id"]]
        for m in metrics:
            bucket[m] += Decimal(r[m])
    return dict(out)


def _fold_by_goods_date(
    rows: list[Any], metrics: tuple[str, ...]
) -> dict[tuple[UUID, date], dict[str, Decimal]]:
    """把全商品版的 (商品, 渠道, 日期) 行合计掉渠道 —— 单商品趋势的形态。"""
    out: dict[tuple[UUID, date], dict[str, Decimal]] = defaultdict(
        lambda: dict.fromkeys(metrics, Decimal("0"))
    )
    for r in rows:
        bucket = out[(r["goods_id"], r["date"])]
        for m in metrics:
            bucket[m] += Decimal(r[m])
    return dict(out)


def _assert_scenario_exercises_all_metrics(
    rows: list[Any], metrics: tuple[str, ...] = _METRICS
) -> None:
    """各指标都必须在场景里出现非零值。

    没有这一步，上面的等值断言可能全是 ``0 == 0``：少建一类数据、或者推广因为
    ``publish_status`` 不是「已发布」被过滤掉，测试照样绿。
    """
    folded = _fold_by_goods(rows, metrics)
    for metric in metrics:
        assert any(
            sums[metric] != Decimal("0") for sums in folded.values()
        ), f"场景没有产生非零的 {metric}，等值断言是空跑"


async def _seed(
    session: AsyncSession,
    tenant: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> dict[str, Any]:
    """四个数据源 × 多商品 × 跨月跨周，含套装与「只有刷单」的边界商品。"""
    # 商品 A：单品，销售 3 天（跨月）+ 广告 2 天 + 刷单 1 天
    style_a = await product_factory.style(style_code=f"SA{uuid4().hex[:6]}")
    goods_a = await _goods(session, tenant, style_a, code=f"G_A_{uuid4().hex[:6]}")
    qn_a = await _platform_product(session, tenant, style_a, goods_a)
    ad_a = await _platform_product(session, tenant, style_a, goods_a, platform="万相台")
    await _qianniu(session, tenant, qn_a, D_MAR_02, pay="1000.00", refund="50.00")
    await _qianniu(session, tenant, qn_a, D_MAR_09, pay="800.00", refund="30.00")
    await _qianniu(session, tenant, qn_a, D_APR_01, pay="600.00")
    await _ad(session, tenant, ad_a, D_MAR_02, cost="120.00", add_cart=5)
    await _ad(session, tenant, ad_a, D_APR_01, cost="90.00")
    await _brushing(session, tenant, style_a, D_MAR_05, amount="200.00")

    # 商品 B：套装（两个款式共用一条销售链接），销售 1 天 + 推广 2 天
    style_b1 = await product_factory.style(style_code=f"SB1{uuid4().hex[:5]}")
    style_b2 = await product_factory.style(style_code=f"SB2{uuid4().hex[:5]}")
    goods_b = await _goods(
        session, tenant, style_b1, style_b2, code=f"G_B_{uuid4().hex[:6]}", is_suit=True
    )
    qn_b = await _platform_product(session, tenant, style_b1, goods_b)
    await _qianniu(session, tenant, qn_b, D_MAR_05, pay="2000.00", refund="100.00")

    # 推广两条，分属不同月份，让 month 桶拆得开。
    # publish_status 必须是「已发布」：报表口径只认已发布的单（PRD V1.4 §9），
    # 工厂默认是「未发布」，漏传这个参数 promo_cost 会全是 0。
    blogger = await blogger_factory.blogger()
    for coop_day, quote in ((D_MAR_05, "400.00"), (D_APR_01, "250.00")):
        await promotion_factory.promotion(
            style=style_b1,
            blogger=blogger,
            goods_main_id=goods_b.id,
            cooperation_date=coop_day,
            quote_amount=Decimal(quote),
            publish_status="已发布",
        )

    # 商品 C：只有刷单，没有销售/广告/推广 —— aggregate_by_goods 的 HAVING 会把它滤掉，
    # 趋势版不会。这个差异必须被显式验证，不能靠默契。
    style_c = await product_factory.style(style_code=f"SC{uuid4().hex[:6]}")
    goods_c = await _goods(session, tenant, style_c, code=f"G_C_{uuid4().hex[:6]}")
    await _brushing(session, tenant, style_c, D_MAR_02, amount="333.00")

    await session.flush()
    return {
        "goods_a": goods_a,
        "goods_b": goods_b,
        "goods_c": goods_c,
        "style_b1": style_b1,
    }


class TestAllGoodsDailyIsFaithfulRefreshSource:
    async def test_daily_sums_match_interval_aggregate(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """逐商品把按日行加起来，必须等于区间聚合 —— 汇总表读取正确性的根本保证。

        不等的话，前端从实时查询切到汇总表的那一刻数字就会跳，而且没有任何报错。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()

            repo = ProductionRepository(session)
            daily = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=LO,
                date_to=HI,
                granularity="day",
                exclude_brushing=True,
            )
            interval = await repo.aggregate_by_goods(
                tenant_id=tenant_a.id,
                date_from=LO,
                date_to=HI,
                exclude_brushing=True,
            )

            _assert_scenario_exercises_all_metrics(daily)
            folded = _fold_by_goods(daily)
            by_id = {r["goods_id"]: r for r in interval}

            # 区间聚合里的每个商品，按日合计必须逐字段相等
            assert by_id, "区间聚合没有数据，测试场景失效"
            for gid, row in by_id.items():
                assert gid in folded, f"商品 {row['goods_code']} 在按日版里缺失"
                for metric in _METRICS:
                    assert folded[gid][metric] == Decimal(row[metric]), (
                        f"{row['goods_code']}.{metric} 口径分叉："
                        f"按日合计 {folded[gid][metric]} != 区间 {row[metric]}"
                    )

            # 按日版多出来的商品，必须确实满足「区间聚合 HAVING 为假」。
            # 商品 C 只有刷单：扣减后 pay_amount 为负，三项原值全 0 → 被 HAVING 滤掉。
            for gid, sums in folded.items():
                if gid in by_id:
                    continue
                assert sums["promo_cost"] == Decimal("0")
                assert sums["ad_spend"] == Decimal("0")
                # 销售额为 0（或被刷单扣成负数）才允许缺席
                assert sums["pay_amount"] <= Decimal(
                    "0"
                ), f"商品 {gid} 有销售额 {sums['pay_amount']} 却没出现在区间聚合里"
            assert seeded["goods_c"].id in folded
            assert seeded["goods_c"].id not in by_id
        finally:
            tenant_id_ctx.reset(tok)

    async def test_all_goods_rows_equal_single_goods_rows(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """全商品版筛出一个商品 == 单商品版。

        前端趋势图现在走单商品路径，切到汇总表后走的是全商品版预聚合的行。
        两者不等的话，同一张图在「切换前/切换后」会画出不同曲线。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            seeded = await _seed(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()

            repo = ProductionRepository(session)
            all_goods = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=LO,
                date_to=HI,
                exclude_brushing=True,
            )
            _assert_scenario_exercises_all_metrics(all_goods, _TREND_METRICS)

            # 全商品版按 (商品, 渠道, 日) 出行，单商品版把渠道合计成一行：先合计再比
            compared = (*_TREND_METRICS, "confirmed_amount", "total_spend")
            folded = _fold_by_goods_date(all_goods, compared)
            rows_per_key: dict[tuple[UUID, date], list[Any]] = defaultdict(list)
            for r in all_goods:
                rows_per_key[(r["goods_id"], r["date"])].append(r)

            # A 覆盖销售/广告/刷单，B 覆盖销售/推广/套装 —— 四个子查询都要对一遍，
            # 只测一个商品会漏掉某个子查询的分组列写错
            for key in ("goods_a", "goods_b"):
                goods = seeded[key]
                single = await repo.daily_trend_by_goods(
                    tenant_id=tenant_a.id,
                    goods_id=goods.id,
                    date_from=LO,
                    date_to=HI,
                    exclude_brushing=True,
                )
                sliced_dates = sorted(d for gid, d in folded if gid == goods.id)
                assert single, f"{key} 没有数据，测试场景失效"
                assert [r["date"] for r in single] == sliced_dates, f"{key} 两条路径日期不一致"
                for want in single:
                    got = folded[(goods.id, want["date"])]
                    for metric in compared:
                        assert got[metric] == Decimal(want[metric]), (
                            f"{key} {want['date']}.{metric}: 全商品版 {got[metric]} "
                            f"!= 单商品版 {want[metric]}"
                        )
                    # 比率不能相加；只有一个渠道的日子才能直接比
                    same_day = rows_per_key[(goods.id, want["date"])]
                    if len(same_day) == 1:
                        assert same_day[0]["net_roi"] == want["net_roi"]
        finally:
            tenant_id_ctx.reset(tok)

    @pytest.mark.parametrize("granularity", ["week", "month"])
    async def test_coarse_buckets_sum_to_day_buckets(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        granularity: str,
    ) -> None:
        """周/月桶的逐商品合计必须等于日桶的合计 —— shop_week/month_summary 的依据。

        日期桶换了，总量不该跟着变。会变通常意味着某个子查询的桶列取错了表
        （比如广告按 a.date 分桶、销售按 q.date 分桶，UNION 之后桶对不上）。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory, blogger_factory, promotion_factory)
            await session.commit()

            repo = ProductionRepository(session)
            day_rows = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=LO,
                date_to=HI,
                granularity="day",
                exclude_brushing=True,
            )
            coarse_rows = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=LO,
                date_to=HI,
                granularity=granularity,
                exclude_brushing=True,
            )

            _assert_scenario_exercises_all_metrics(day_rows, _TREND_METRICS)
            by_day = _fold_by_goods(day_rows, _TREND_METRICS)
            by_coarse = _fold_by_goods(coarse_rows, _TREND_METRICS)
            assert by_day and by_day.keys() == by_coarse.keys()
            for gid, sums in by_day.items():
                for metric in _TREND_METRICS:
                    assert by_coarse[gid][metric] == sums[metric], (
                        f"{granularity} 桶的 {metric} 合计 {by_coarse[gid][metric]} "
                        f"!= day 桶 {sums[metric]}"
                    )
            # 桶真的变粗了，否则这条测试在空跑
            assert len(coarse_rows) < len(day_rows)
        finally:
            tenant_id_ctx.reset(tok)


class TestOneRowServesBothBrushingModes:
    """汇总表只存一份行，两种「剔除刷单」口径都要能从它还原。

    报表有「含刷单 / 剔刷单」开关。如果汇总表只存其中一个口径的 pay_amount，
    另一个口径就查不了；为开关各存一份行则是把同一笔数据写两遍，迟早对不上。

    做法是额外存 ``brushing_amount``：``pay_amount`` 是含刷单的原值，
    剔刷单口径 = ``pay_amount - brushing_amount``。这两条测试就是这个等式的证明。
    """

    async def test_brushing_amount_reconstructs_excluded_mode(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory, blogger_factory, promotion_factory)
            await session.commit()

            repo = ProductionRepository(session)
            included = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=LO,
                date_to=HI,
                exclude_brushing=False,
            )
            excluded = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=LO,
                date_to=HI,
                exclude_brushing=True,
            )

            # 行集必须一致：汇总表存的是「同一批 (商品, 日期)」，
            # 两个口径只差一次减法，不该差出几行来
            keys_in = {(r["goods_id"], r["channel"], r["date"]) for r in included}
            keys_ex = {(r["goods_id"], r["channel"], r["date"]) for r in excluded}
            assert keys_in == keys_ex

            by_key_ex = {(r["goods_id"], r["channel"], r["date"]): r for r in excluded}
            brushed = 0
            for row in included:
                want = by_key_ex[(row["goods_id"], row["channel"], row["date"])]
                # 从「含刷单」的行还原「剔刷单」—— 这就是读取汇总表时要做的运算
                reconstructed = Decimal(row["pay_amount"]) - Decimal(row["brushing_amount"])
                assert reconstructed == Decimal(want["pay_amount"]), (
                    f"{row['date']} 还原失败：{row['pay_amount']} - "
                    f"{row['brushing_amount']} != {want['pay_amount']}"
                )
                # 刷单之外的指标不该受开关影响
                for metric in ("refund_amount", "promo_cost", "ad_spend"):
                    assert Decimal(row[metric]) == Decimal(want[metric])
                if Decimal(row["brushing_amount"]) != Decimal("0"):
                    brushed += 1

            # 场景里真的有刷单行，否则上面的减法全是减 0
            assert brushed > 0, "场景没有刷单数据，还原等式是空跑"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_included_mode_pay_amount_is_raw(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
    ) -> None:
        """exclude_brushing=False 时 pay_amount 必须是未扣减的原值。

        brushing CTE 改成「总是计算」之后，最容易出的错是减法条件写漏，
        让含刷单口径也被扣了一次 —— 数字会静默变小。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code=f"RAW{uuid4().hex[:6]}")
            goods = await _goods(session, tenant_a, style, code=f"G_RAW_{uuid4().hex[:6]}")
            pp = await _platform_product(session, tenant_a, style, goods)
            await _qianniu(session, tenant_a, pp, D_MAR_02, pay="1000.00")
            await _brushing(session, tenant_a, style, D_MAR_02, amount="300.00")
            await session.commit()

            repo = ProductionRepository(session)
            rows = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=goods.id,
                date_from=D_MAR_02,
                date_to=D_MAR_02,
                exclude_brushing=False,
            )
            assert len(rows) == 1
            assert Decimal(rows[0]["pay_amount"]) == Decimal("1000.00")
            assert Decimal(rows[0]["brushing_amount"]) == Decimal("300.00")

            excluded = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=goods.id,
                date_from=D_MAR_02,
                date_to=D_MAR_02,
                exclude_brushing=True,
            )
            assert Decimal(excluded[0]["pay_amount"]) == Decimal("700.00")
        finally:
            tenant_id_ctx.reset(tok)


async def _seed_channels(
    session: AsyncSession,
    tenant: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> GoodsMain:
    """同一商品：普通 + 直播两条千牛链接、一条直播万相台链接，另有推广与刷单。

    D_MAR_02 两个渠道都有销售，D_MAR_09 只有直播 —— 单商品趋势在 D_MAR_02 要把
    两行合成一行，在 D_MAR_09 照旧一行。
    """
    style = await product_factory.style(style_code=f"CH{uuid4().hex[:6]}")
    goods = await _goods(session, tenant, style, code=f"G_CH_{uuid4().hex[:6]}")
    qn_normal = await _platform_product(session, tenant, style, goods)
    qn_live = await _platform_product(session, tenant, style, goods, channel="直播")
    ad_live = await _platform_product(
        session, tenant, style, goods, platform="万相台", channel="直播"
    )
    await _qianniu(
        session, tenant, qn_normal, D_MAR_02, pay="1000.00", refund="40.00", pay_orders=3
    )
    await _qianniu(session, tenant, qn_live, D_MAR_02, pay="500.00", refund="10.00", pay_orders=2)
    await _qianniu(session, tenant, qn_live, D_MAR_09, pay="300.00", pay_orders=4)
    await _ad(session, tenant, ad_live, D_MAR_02, cost="80.00", add_cart=7)
    await _brushing(session, tenant, style, D_MAR_02, amount="100.00")
    blogger = await blogger_factory.blogger()
    await promotion_factory.promotion(
        style=style,
        blogger=blogger,
        goods_main_id=goods.id,
        cooperation_date=D_MAR_02,
        quote_amount=Decimal("200.00"),
        publish_status="已发布",
    )
    await session.flush()
    return goods


class TestChannelDimension:
    """062 起 ``product_roi_summary`` 按 (商品, 渠道, 日) 存（7e R2）。

    渠道来自链接：销售按千牛链接、站内花费按万相台链接；推广与刷单没有链接，记普通。
    单商品趋势在 R2 不拆渠道（投产接口结构不变），要把渠道合计回一行。
    """

    async def test_all_goods_rows_split_by_link_channel(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            goods = await _seed_channels(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()

            rows = await ProductionRepository(session).daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=LO,
                date_to=HI,
                exclude_brushing=False,
            )
            mine = {(r["channel"], r["date"]): r for r in rows if r["goods_id"] == goods.id}
            assert set(mine) == {("普通", D_MAR_02), ("直播", D_MAR_02), ("直播", D_MAR_09)}

            normal = mine[("普通", D_MAR_02)]
            assert Decimal(normal["pay_amount"]) == Decimal("1000.00")
            assert Decimal(normal["refund_amount"]) == Decimal("40.00")
            assert int(normal["pay_orders"]) == 3
            # 推广、刷单记普通
            assert Decimal(normal["brushing_amount"]) == Decimal("100.00")
            assert Decimal(normal["promo_cost"]) > 0
            assert Decimal(normal["ad_spend"]) == 0
            assert int(normal["ad_add_cart_count"]) == 0

            live = mine[("直播", D_MAR_02)]
            assert Decimal(live["pay_amount"]) == Decimal("500.00")
            assert Decimal(live["refund_amount"]) == Decimal("10.00")
            assert int(live["pay_orders"]) == 2
            # 站内花费与站内加购按万相台链接的渠道
            assert Decimal(live["ad_spend"]) == Decimal("80.00")
            assert int(live["ad_add_cart_count"]) == 7
            assert Decimal(live["brushing_amount"]) == 0
            assert Decimal(live["promo_cost"]) == 0

            assert int(mine[("直播", D_MAR_09)]["pay_orders"]) == 4
        finally:
            tenant_id_ctx.reset(tok)

    @pytest.mark.parametrize("granularity", ["day", "week"])
    async def test_single_goods_sums_channels_into_one_row(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
        granularity: str,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            goods = await _seed_channels(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()

            repo = ProductionRepository(session)
            all_goods = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=None,
                date_from=LO,
                date_to=HI,
                granularity=granularity,
                exclude_brushing=True,
            )
            single = await repo.daily_trend_by_goods(
                tenant_id=tenant_a.id,
                goods_id=goods.id,
                date_from=LO,
                date_to=HI,
                granularity=granularity,
                exclude_brushing=True,
            )
            compared = (*_TREND_METRICS, "confirmed_amount", "total_spend")
            folded = _fold_by_goods_date(
                [r for r in all_goods if r["goods_id"] == goods.id], compared
            )
            # 每个桶一行（D_MAR_02 / D_MAR_09 分属两个 ISO 周），且 = 两个渠道之和
            assert [r["date"] for r in single] == sorted(d for _, d in folded)
            assert len(single) == 2
            for want in single:
                got = folded[(goods.id, want["date"])]
                for metric in compared:
                    assert got[metric] == Decimal(
                        want[metric]
                    ), f"{want['date']}.{metric}: 渠道合计 {got[metric]} != 单商品 {want[metric]}"
            first = single[0]
            assert Decimal(first["pay_amount"]) == Decimal("1400.00")  # 1000 + 500 - 刷单 100
            assert int(first["pay_orders"]) == 5
            assert int(first["ad_add_cart_count"]) == 7
        finally:
            tenant_id_ctx.reset(tok)

    async def test_refresh_writes_channel_and_new_columns(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            goods = await _seed_channels(
                session, tenant_a, product_factory, blogger_factory, promotion_factory
            )
            await session.commit()

            await SummaryRefreshService(session).refresh(
                tenant_id=tenant_a.id, date_from=D_MAR_02, date_to=D_MAR_09
            )
            await session.commit()

            stored = (
                (
                    await session.execute(
                        select(ProductRoiSummary).where(
                            ProductRoiSummary.tenant_id == tenant_a.id,
                            ProductRoiSummary.goods_main_id == goods.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_key = {(s.channel, s.stat_date): s for s in stored}
            assert set(by_key) == {("普通", D_MAR_02), ("直播", D_MAR_02), ("直播", D_MAR_09)}
            normal, live = by_key[("普通", D_MAR_02)], by_key[("直播", D_MAR_02)]
            assert (normal.pay_orders, normal.ad_add_cart_count) == (3, 0)
            assert normal.brushing_amount == Decimal("100.00")
            assert (live.pay_orders, live.ad_add_cart_count) == (2, 7)
            assert live.ad_spend == Decimal("80.00")
            assert live.pay_amount == Decimal("500.00")
            assert by_key[("直播", D_MAR_09)].pay_orders == 4
        finally:
            tenant_id_ctx.reset(tok)
