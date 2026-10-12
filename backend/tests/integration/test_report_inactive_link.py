"""7e-9 停用链接：``platform_product.is_active = false`` 的链接只从单品口径里排除。

口径（报表视图设计 2.9）：
- 停用千牛链接的日报（销售、退款、加购、extra）、停用万相台链接的站内花费，不进单品
  —— 投产主表、趋势、extra 取数、``product_roi_summary`` 刷新行四处同一口径；
- 店铺日报与 BI 店铺总览照旧全店，停用链接的数字照算；
- legacy 兜底（款式上手填千牛 ID、没建链接）的 ``NOT EXISTS`` 不看 ``is_active``：
  停用的链接照样挡住兜底，否则停用之后反而被兜底算回单品。

每条用例都放一条启用的对照链接：单品数字非零且恰好等于对照那部分，店铺数字等于两者之和，
等值断言才不是 ``0 == 0`` 空跑。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import AdDaily, QianniuDaily
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.advanced_repository import (
    BiRepository,
    ProductionRepository,
    StoreDailyRepository,
)
from app.modules.report.summary_models import ProductRoiSummary
from app.modules.report.summary_refresh_service import SummaryRefreshService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 远离其他用例的日期，店铺 / BI 的全租户合计只含本用例的数据
D1 = date(2026, 5, 11)
D2 = date(2026, 5, 12)


async def _goods(session: AsyncSession, tenant: Any, style: Any, *, code: str) -> GoodsMain:
    goods = GoodsMain(tenant_id=tenant.id, goods_code=code, goods_title=f"{code} 商品")
    session.add(goods)
    await session.flush()
    session.add(
        GoodsStyleItem(tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id, sort_order=0)
    )
    await session.flush()
    return goods


async def _link(
    session: AsyncSession,
    tenant: Any,
    style: Any,
    goods: GoodsMain,
    *,
    platform: str = "千牛",
    is_active: bool = True,
    platform_id: str | None = None,
) -> PlatformProduct:
    pp = PlatformProduct(
        tenant_id=tenant.id,
        platform=platform,
        platform_id=platform_id or f"P{uuid4().hex[:10]}",
        style_id=style.id,
        goods_main_id=goods.id,
        is_active=is_active,
    )
    session.add(pp)
    await session.flush()
    return pp


def _qianniu(
    session: AsyncSession,
    tenant: Any,
    platform_id: str,
    day: date,
    *,
    pay: str,
    refund: str,
    cart: int,
    tag: str,
    platform_product_id: Any = None,
) -> None:
    """``platform_product_id`` 为空时走 platform_id 快照对链接（日报没回填 FK 的情形）。"""
    session.add(
        QianniuDaily(
            tenant_id=tenant.id,
            platform_product_id=platform_product_id,
            platform_id_snapshot=platform_id,
            date=day,
            visitors=10,
            pay_amount=Decimal(pay),
            pay_orders=1,
            refund_amount=Decimal(refund),
            add_cart_count=cart,
            extra={"tag": tag},
        )
    )


def _ad(
    session: AsyncSession,
    tenant: Any,
    platform_id: str,
    day: date,
    *,
    cost: str,
    tag: str,
    platform_product_id: Any = None,
) -> None:
    session.add(
        AdDaily(
            tenant_id=tenant.id,
            platform_product_id=platform_product_id,
            platform_id_snapshot=platform_id,
            date=day,
            cost=Decimal(cost),
            extra={"tag": tag},
        )
    )


async def _goods_views(session: AsyncSession, tenant: Any, goods: GoodsMain) -> dict[str, Any]:
    """单品口径的四处取数：主表、趋势（单商品 / 全商品）、extra、刷新后的汇总行。"""
    repo = ProductionRepository(session)
    main = {
        r["goods_id"]: r
        for r in await repo.aggregate_by_goods(
            tenant_id=tenant.id, date_from=D1, date_to=D2, exclude_brushing=False
        )
    }
    single = await repo.daily_trend_by_goods(
        tenant_id=tenant.id,
        goods_id=goods.id,
        date_from=D1,
        date_to=D2,
        exclude_brushing=False,
    )
    all_goods = [
        r
        for r in await repo.daily_trend_by_goods(
            tenant_id=tenant.id,
            goods_id=None,
            date_from=D1,
            date_to=D2,
            exclude_brushing=False,
        )
        if r["goods_id"] == goods.id
    ]
    extra = [
        (r["src"], r["extra"]["tag"])
        for r in await repo.fetch_extra_by_goods(tenant_id=tenant.id, date_from=D1, date_to=D2)
        if r["goods_id"] == goods.id
    ]
    await SummaryRefreshService(session).refresh(tenant_id=tenant.id, date_from=D1, date_to=D2)
    stored = (
        (
            await session.execute(
                select(ProductRoiSummary).where(
                    ProductRoiSummary.tenant_id == tenant.id,
                    ProductRoiSummary.goods_main_id == goods.id,
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "main": main.get(goods.id),
        "single": single,
        "all": all_goods,
        "extra": sorted(extra),
        "stored": stored,
    }


def _sum(rows: Any, key: str) -> Decimal:
    return sum((Decimal(r[key]) for r in rows), Decimal("0"))


def _sum_attr(rows: Any, key: str) -> Decimal:
    return sum((Decimal(getattr(r, key)) for r in rows), Decimal("0"))


class TestInactiveLink:
    async def test_inactive_qianniu_link_excluded_from_goods_kept_in_store(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """停用千牛链接的日报（FK 与快照两条路）不进单品四处，照进店铺日报与 BI 店铺总览。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code=f"IL{uuid4().hex[:6]}")
            goods = await _goods(session, tenant_a, style, code=f"G_IL_{uuid4().hex[:6]}")
            on = await _link(session, tenant_a, style, goods)
            off = await _link(session, tenant_a, style, goods, is_active=False)
            _qianniu(
                session,
                tenant_a,
                on.platform_id,
                D1,
                pay="1000.00",
                refund="10.00",
                cart=3,
                tag="on",
                platform_product_id=on.id,
            )
            # 停用链接：D1 带 FK，D2 只有快照 —— D2 整天只有停用链接的数据
            _qianniu(
                session,
                tenant_a,
                off.platform_id,
                D1,
                pay="500.00",
                refund="20.00",
                cart=4,
                tag="off_fk",
                platform_product_id=off.id,
            )
            _qianniu(
                session,
                tenant_a,
                off.platform_id,
                D2,
                pay="300.00",
                refund="5.00",
                cart=6,
                tag="off_snap",
            )
            await session.flush()

            v = await _goods_views(session, tenant_a, goods)
            main = v["main"]
            assert main is not None
            assert main["pay_amount"] == Decimal("1000.00")
            assert main["refund_amount"] == Decimal("10.00")
            assert int(main["add_cart_count"]) == 3
            assert [r["date"] for r in v["single"]] == [D1]
            assert _sum(v["single"], "pay_amount") == Decimal("1000.00")
            assert [r["date"] for r in v["all"]] == [D1]
            assert _sum(v["all"], "refund_amount") == Decimal("10.00")
            assert _sum(v["all"], "add_cart_count") == Decimal("3")
            assert v["extra"] == [("qianniu", "on")]
            assert [s.stat_date for s in v["stored"]] == [D1]
            assert _sum_attr(v["stored"], "pay_amount") == Decimal("1000.00")
            assert _sum_attr(v["stored"], "refund_amount") == Decimal("10.00")

            # 店铺口径全店：停用链接照算，只有停用数据的 D2 也在
            store = {
                r["date"]: r
                for r in await StoreDailyRepository(session).aggregate(
                    tenant_id=tenant_a.id, date_from=D1, date_to=D2
                )
            }
            assert store[D1]["pay_amount"] == Decimal("1500.00")
            assert store[D2]["pay_amount"] == Decimal("300.00")
            bi = await BiRepository(session).aggregate_store_summary(
                tenant_id=tenant_a.id, date_from=D1, date_to=D2
            )
            assert bi["sales_amount"] == Decimal("1800.00")
            assert bi["refund_amount"] == Decimal("35.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_inactive_link_still_blocks_legacy_fallback(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """款式上手填了千牛 ID、同 ID 的链接已停用：日报既不走正路，也不能被兜底算回单品。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            qn_id = f"QN_LEG_{uuid4().hex[:8]}"
            style = await product_factory.style(
                style_code=f"LG{uuid4().hex[:6]}", qianniu_product_id=qn_id
            )
            goods = await _goods(session, tenant_a, style, code=f"G_LG_{uuid4().hex[:6]}")
            await _link(session, tenant_a, style, goods, is_active=False, platform_id=qn_id)
            # 兜底只认没回填 FK 的日报
            _qianniu(
                session, tenant_a, qn_id, D1, pay="700.00", refund="7.00", cart=2, tag="legacy"
            )
            await session.flush()

            v = await _goods_views(session, tenant_a, goods)
            assert v["main"] is None
            assert v["single"] == []
            assert v["all"] == []
            assert v["extra"] == []
            assert v["stored"] == []

            store = await StoreDailyRepository(session).aggregate(
                tenant_id=tenant_a.id, date_from=D1, date_to=D2
            )
            assert [(r["date"], r["pay_amount"]) for r in store] == [(D1, Decimal("700.00"))]
        finally:
            tenant_id_ctx.reset(token)

    async def test_inactive_wanxiangtai_link_spend_excluded_from_goods(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """停用万相台链接的花费（FK 与快照两条路）不进单品，BI 店铺站内花费照算。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style(style_code=f"IW{uuid4().hex[:6]}")
            goods = await _goods(session, tenant_a, style, code=f"G_IW_{uuid4().hex[:6]}")
            qn = await _link(session, tenant_a, style, goods)
            ad_on = await _link(session, tenant_a, style, goods, platform="万相台")
            ad_off = await _link(
                session, tenant_a, style, goods, platform="万相台", is_active=False
            )
            _qianniu(
                session,
                tenant_a,
                qn.platform_id,
                D1,
                pay="100.00",
                refund="0",
                cart=1,
                tag="qn",
                platform_product_id=qn.id,
            )
            _ad(
                session,
                tenant_a,
                ad_on.platform_id,
                D1,
                cost="50.00",
                tag="ad_on",
                platform_product_id=ad_on.id,
            )
            _ad(
                session,
                tenant_a,
                ad_off.platform_id,
                D1,
                cost="70.00",
                tag="ad_off_fk",
                platform_product_id=ad_off.id,
            )
            _ad(session, tenant_a, ad_off.platform_id, D2, cost="30.00", tag="ad_off_snap")
            await session.flush()

            v = await _goods_views(session, tenant_a, goods)
            main = v["main"]
            assert main is not None
            assert main["ad_spend"] == Decimal("50.00")
            assert _sum(v["single"], "ad_spend") == Decimal("50.00")
            assert [r["date"] for r in v["all"]] == [D1]
            assert _sum(v["all"], "ad_spend") == Decimal("50.00")
            assert v["extra"] == [("ad", "ad_on"), ("qianniu", "qn")]
            assert _sum_attr(v["stored"], "ad_spend") == Decimal("50.00")

            bi = await BiRepository(session).aggregate_store_summary(
                tenant_id=tenant_a.id, date_from=D1, date_to=D2
            )
            assert bi["internal_spend"] == Decimal("150.00")
        finally:
            tenant_id_ctx.reset(token)


# 停用链接数（7e R3，计划决定 10）的区间：远离上面的 D1 / D2 与其他用例
C1 = date(2026, 4, 6)
C2 = date(2026, 4, 7)
OUTSIDE = date(2026, 4, 9)


async def _count(session: AsyncSession, tenant: Any, *, seasons: list[str] | None = None) -> int:
    return await ProductionRepository(session).inactive_link_count(
        tenant_id=tenant.id, date_from=C1, date_to=C2, seasons=seasons
    )


class TestInactiveLinkCount:
    """``inactive_link_count``：区间内有日报行或花费行的停用链接数（金额 0 的行也算）。

    每条用例都有一条启用的对照链接带数据：启用链接不算，计数才不是「什么都数」。
    """

    async def _setup(
        self, session: AsyncSession, tenant: Any, product_factory: Any, *, season: str = "春夏"
    ) -> tuple[Any, GoodsMain]:
        style = await product_factory.style(style_code=f"IC{uuid4().hex[:6]}")
        goods = await _goods(session, tenant, style, code=f"G_IC_{uuid4().hex[:6]}")
        goods.season = season
        on = await _link(session, tenant, style, goods)
        _qianniu(
            session,
            tenant,
            on.platform_id,
            C1,
            pay="100.00",
            refund="0",
            cart=1,
            tag="on",
            platform_product_id=on.id,
        )
        await session.flush()
        return style, goods

    async def test_zero_amount_daily_row_counts(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """停用千牛链接区间内一行金额 0 的日报（只有快照、没回填 FK）→ 1。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await self._setup(session, tenant_a, product_factory)
            off = await _link(session, tenant_a, style, goods, is_active=False)
            _qianniu(session, tenant_a, off.platform_id, C2, pay="0", refund="0", cart=0, tag="z")
            await session.flush()
            assert await _count(session, tenant_a) == 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_wanxiangtai_spend_only_counts(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """停用万相台链接只有花费（FK 一行、快照一行）→ 1，同一条链接不重复数。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await self._setup(session, tenant_a, product_factory)
            off = await _link(session, tenant_a, style, goods, platform="万相台", is_active=False)
            _ad(
                session,
                tenant_a,
                off.platform_id,
                C1,
                cost="12.00",
                tag="fk",
                platform_product_id=off.id,
            )
            _ad(session, tenant_a, off.platform_id, C2, cost="3.00", tag="snap")
            await session.flush()
            assert await _count(session, tenant_a) == 1
        finally:
            tenant_id_ctx.reset(token)

    async def test_two_links_and_cross_platform_snapshot(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """两条停用链接各有数据 → 2；万相台日报的快照碰上停用千牛链接的 ID 不算千牛那条。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await self._setup(session, tenant_a, product_factory)
            qn_off = await _link(session, tenant_a, style, goods, is_active=False)
            ad_off = await _link(
                session, tenant_a, style, goods, platform="万相台", is_active=False
            )
            idle = await _link(session, tenant_a, style, goods, is_active=False)
            _qianniu(
                session,
                tenant_a,
                qn_off.platform_id,
                C1,
                pay="50.00",
                refund="0",
                cart=0,
                tag="qn",
                platform_product_id=qn_off.id,
            )
            _ad(
                session,
                tenant_a,
                ad_off.platform_id,
                C2,
                cost="5.00",
                tag="ad",
                platform_product_id=ad_off.id,
            )
            # 万相台日报的快照恰好是第三条（停用千牛）链接的 ID：平台不同，不算
            _ad(session, tenant_a, idle.platform_id, C1, cost="1.00", tag="x")
            await session.flush()
            assert await _count(session, tenant_a) == 2
        finally:
            tenant_id_ctx.reset(token)

    async def test_outside_range_or_unbound_not_counted(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """数据只在区间外 → 0；没有归属商品的停用链接 → 0；启用链接 → 0。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await self._setup(session, tenant_a, product_factory)
            off = await _link(session, tenant_a, style, goods, is_active=False)
            _qianniu(
                session,
                tenant_a,
                off.platform_id,
                OUTSIDE,
                pay="80.00",
                refund="0",
                cart=1,
                tag="out",
                platform_product_id=off.id,
            )
            unbound = await _link(session, tenant_a, style, goods, is_active=False)
            unbound.goods_main_id = None
            _qianniu(
                session,
                tenant_a,
                unbound.platform_id,
                C1,
                pay="80.00",
                refund="0",
                cart=1,
                tag="unbound",
                platform_product_id=unbound.id,
            )
            await session.flush()
            assert await _count(session, tenant_a) == 0
            # 场景有效性：把区间放宽到 OUTSIDE，同一条链接就算上了
            assert (
                await ProductionRepository(session).inactive_link_count(
                    tenant_id=tenant_a.id, date_from=C1, date_to=OUTSIDE, seasons=None
                )
                == 1
            )
        finally:
            tenant_id_ctx.reset(token)

    async def test_season_filter_and_deleted_goods(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """季节筛掉 → 0、季节命中 → 1；商品已删 → 0。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await self._setup(session, tenant_a, product_factory)
            off = await _link(session, tenant_a, style, goods, is_active=False)
            _qianniu(
                session,
                tenant_a,
                off.platform_id,
                C1,
                pay="20.00",
                refund="0",
                cart=0,
                tag="s",
                platform_product_id=off.id,
            )
            await session.flush()
            assert await _count(session, tenant_a, seasons=["秋冬"]) == 0
            assert await _count(session, tenant_a, seasons=["春夏"]) == 1
            assert await _count(session, tenant_a, seasons=[]) == 1
            goods.is_deleted = True
            await session.flush()
            assert await _count(session, tenant_a) == 0
        finally:
            tenant_id_ctx.reset(token)
