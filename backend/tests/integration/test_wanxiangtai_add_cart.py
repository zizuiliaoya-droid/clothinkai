"""万相台站内加购（062）：真实导出 → adapter → ``ad_daily.add_cart_count``；投产刷新源的逐日配对加购。

adapter 部分照 ``test_qianniu_refund_cart.py``：**不手写 ad_daily 行**，从照真实导出抄来的
整行（76 列中文表头，见 ``tests/unit/test_extra_metrics.py``）开始，改成千分位、``-``、
缺列等脏值，走 parse_row → validate → upsert。

配对部分（设计 3.2）：同一（商品, 渠道, 日）千牛与万相台都有行才配对，逐日判断，
按天存原值；粗桶与单商品趋势 = 逐日配对结果之和。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.collect.models import AdDaily, QianniuDaily
from app.modules.importer.adapters.wanxiangtai import _DEFAULT_COLUMNS, WanxiangtaiImportAdapter
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.advanced_repository import ProductionRepository
from app.modules.report.summary_models import ProductRoiSummary
from app.modules.report.summary_refresh_service import SummaryRefreshService
from tests.unit.test_extra_metrics import wanxiangtai_row

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 2026-03-02 是周一，三天同一 ISO 周
D1, D2, D3 = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)
LO, HI = date(2026, 3, 1), date(2026, 3, 31)
_PAIRED = ("paired_all_cart", "paired_ad_cart", "paired_days")


async def _goods(session: AsyncSession, tenant: Any, product_factory: Any) -> tuple[Any, GoodsMain]:
    style = await product_factory.style(style_code=f"WX{uuid4().hex[:6]}")
    goods = GoodsMain(
        tenant_id=tenant.id, goods_code=f"G_WX_{uuid4().hex[:6]}", goods_title=style.style_name
    )
    session.add(goods)
    await session.flush()
    session.add(GoodsStyleItem(tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id))
    await session.flush()
    return style, goods


async def _link(
    session: AsyncSession,
    tenant: Any,
    style: Any,
    goods: GoodsMain,
    *,
    platform: str,
    channel: str = "普通",
) -> PlatformProduct:
    pp = PlatformProduct(
        tenant_id=tenant.id,
        platform=platform,
        platform_id=f"P{uuid4().hex[:10]}",
        style_id=style.id,
        goods_main_id=goods.id,
        channel=channel,
    )
    session.add(pp)
    await session.flush()
    return pp


async def _import(
    session: AsyncSession, tenant: Any, rows: list[dict[str, Any]], mapping: Any = None
) -> None:
    adapter = WanxiangtaiImportAdapter()
    for raw in rows:
        parsed = adapter.parse_row(raw, mapping)
        assert adapter.validate(parsed) == []
        await adapter.upsert(parsed, session=session, tenant_id=tenant.id, actor_id=None)
    await session.flush()


async def _stored_cart(session: AsyncSession, pid: str) -> dict[date, int | None]:
    rows = (
        await session.execute(
            select(AdDaily.date, AdDaily.add_cart_count).where(AdDaily.platform_id_snapshot == pid)
        )
    ).all()
    return {r.date: r.add_cart_count for r in rows}


class TestAdapterAddCart:
    async def test_real_export_parsed_into_typed_column(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await _goods(session, tenant_a, product_factory)
            pid = (await _link(session, tenant_a, style, goods, platform="万相台")).platform_id
            # 样本第 0 行：直接 / 间接购物车数是 5 / 3，只能读「总购物车数」
            day1 = wanxiangtai_row(0, 日期=D1.isoformat(), 主体ID=pid, 总购物车数="1,271")
            day2 = wanxiangtai_row(1, 日期=D2.isoformat(), 主体ID=pid, 总购物车数="-")
            day3 = wanxiangtai_row(2, 日期=D3.isoformat(), 主体ID=pid)
            del day3["总购物车数"]
            await _import(session, tenant_a, [day1, day2, day3])

            assert await _stored_cart(session, pid) == {D1: 1271, D2: None, D3: None}
            extra = (
                await session.execute(
                    select(AdDaily.extra).where(
                        AdDaily.platform_id_snapshot == pid, AdDaily.date == D1
                    )
                )
            ).scalar_one()
            assert extra is not None
            assert extra["总购物车数"] == "1,271"
            # 同 055 的理由：不进默认映射清单（租户映射会整体替换它）
            assert all(c["target_field"] != "add_cart_count" for c in _DEFAULT_COLUMNS)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_reimport_overwrites_add_cart(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """同一天重导（ON CONFLICT 那条路）：新数覆盖旧数；新导出是 "-" 就清成 NULL。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await _goods(session, tenant_a, product_factory)
            pid = (await _link(session, tenant_a, style, goods, platform="万相台")).platform_id
            await _import(session, tenant_a, [wanxiangtai_row(0, 日期=D1.isoformat(), 主体ID=pid)])
            assert await _stored_cart(session, pid) == {D1: 8}
            await _import(
                session,
                tenant_a,
                [wanxiangtai_row(0, 日期=D1.isoformat(), 主体ID=pid, 总购物车数="1,300")],
            )
            assert await _stored_cart(session, pid) == {D1: 1300}
            await _import(
                session,
                tenant_a,
                [wanxiangtai_row(0, 日期=D1.isoformat(), 主体ID=pid, 总购物车数="-")],
            )
            assert await _stored_cart(session, pid) == {D1: None}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_mapping_wins_over_standard_header(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await _goods(session, tenant_a, product_factory)
            pid = (await _link(session, tenant_a, style, goods, platform="万相台")).platform_id
            mapping = cast(
                Any,
                SimpleNamespace(
                    mapping_config={
                        "columns": [
                            *_DEFAULT_COLUMNS,
                            {
                                "source_col": "自定义加购",
                                "target_field": "add_cart_count",
                                "type": "int",
                            },
                        ]
                    }
                ),
            )
            rows = [
                wanxiangtai_row(0, 日期=D1.isoformat(), 主体ID=pid, 自定义加购="2,048"),
                # 映射配了但这行是占位符：按映射读成 NULL，不回落到标准表头的 8
                wanxiangtai_row(0, 日期=D2.isoformat(), 主体ID=pid, 自定义加购="-"),
            ]
            await _import(session, tenant_a, rows, mapping)
            assert await _stored_cart(session, pid) == {D1: 2048, D2: None}
        finally:
            tenant_id_ctx.reset(tok)


async def _qn(
    session: AsyncSession, tenant: Any, pp: PlatformProduct, day: date, *, cart: int | None
) -> None:
    session.add(
        QianniuDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            visitors=10,
            pay_amount=Decimal("100.00"),
            pay_orders=1,
            refund_amount=Decimal("0"),
            add_cart_count=cart,
        )
    )


async def _ad(
    session: AsyncSession, tenant: Any, pp: PlatformProduct, day: date, *, cart: int | None
) -> None:
    session.add(
        AdDaily(
            tenant_id=tenant.id,
            platform_product_id=pp.id,
            platform_id_snapshot=pp.platform_id,
            date=day,
            cost=Decimal("10.00"),
            add_cart_count=cart,
            extra={},
        )
    )


async def _trend(
    session: AsyncSession,
    tenant: Any,
    goods_id: UUID | None,
    *,
    granularity: str = "day",
) -> list[Any]:
    return list(
        await ProductionRepository(session).daily_trend_by_goods(
            tenant_id=tenant.id,
            goods_id=goods_id,
            date_from=LO,
            date_to=HI,
            granularity=granularity,
            exclude_brushing=False,
        )
    )


def _paired(row: Any) -> tuple[int, int, int]:
    return cast(tuple[int, int, int], tuple(int(row[m]) for m in _PAIRED))


class TestPairedCart:
    async def test_pairs_day_by_day(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """千牛 D1、D2 + 万相台只有 D2（另有万相台独有的 D3）→ 只有 D2 配对。

        整段区间判一次「两边都有」会把 D1 的千牛加购也算进来（paired_days = 2）。
        """
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await _goods(session, tenant_a, product_factory)
            qn = await _link(session, tenant_a, style, goods, platform="千牛")
            ad = await _link(session, tenant_a, style, goods, platform="万相台")
            await _qn(session, tenant_a, qn, D1, cart=10)
            await _qn(session, tenant_a, qn, D2, cart=6)
            await _ad(session, tenant_a, ad, D2, cart=4)
            await _ad(session, tenant_a, ad, D3, cart=9)
            await session.flush()

            rows = {
                r["date"]: r
                for r in await _trend(session, tenant_a, None)
                if r["goods_id"] == goods.id
            }
            assert {d: _paired(r) for d, r in rows.items()} == {
                D1: (0, 0, 0),
                D2: (6, 4, 1),
                D3: (0, 0, 0),
            }
            # 场景有效性：D1 / D3 各自那一边的加购确实在（不是两边都空才没配上）
            assert int(rows[D1]["add_cart_count"]) == 10
            assert int(rows[D3]["ad_add_cart_count"]) == 9

            # 单商品趋势按周：一桶 = 逐日配对之和，不是整周重判
            (week,) = await _trend(session, tenant_a, goods.id, granularity="week")
            assert _paired(week) == (6, 4, 1)
            assert (int(week["add_cart_count"]), int(week["ad_add_cart_count"])) == (16, 13)

            # 刷新照存三列
            await SummaryRefreshService(session).refresh(
                tenant_id=tenant_a.id, date_from=D1, date_to=D3
            )
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
            by_day = {
                s.stat_date: (s.paired_all_cart, s.paired_ad_cart, s.paired_days) for s in stored
            }
            assert by_day == {D1: (0, 0, 0), D2: (6, 4, 1), D3: (0, 0, 0)}
        finally:
            tenant_id_ctx.reset(tok)

    async def test_same_day_rows_pair_even_without_cart_value(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """两边当天都有行就配对；万相台那行加购是 NULL（导出是 "-"）也算有行，记 0。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await _goods(session, tenant_a, product_factory)
            qn = await _link(session, tenant_a, style, goods, platform="千牛")
            ad = await _link(session, tenant_a, style, goods, platform="万相台")
            await _qn(session, tenant_a, qn, D1, cart=12)
            await _ad(session, tenant_a, ad, D1, cart=None)
            await session.flush()

            rows = [r for r in await _trend(session, tenant_a, None) if r["goods_id"] == goods.id]
            assert len(rows) == 1
            assert _paired(rows[0]) == (12, 0, 1)
        finally:
            tenant_id_ctx.reset(tok)

    async def test_daily_differences_kept_raw(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """按天存原值：D1 千牛比站内多 5、D2 少 8 → 两列之差合计 −3（先求和、读取侧再取 0）。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await _goods(session, tenant_a, product_factory)
            qn = await _link(session, tenant_a, style, goods, platform="千牛")
            ad = await _link(session, tenant_a, style, goods, platform="万相台")
            await _qn(session, tenant_a, qn, D1, cart=15)
            await _ad(session, tenant_a, ad, D1, cart=10)
            await _qn(session, tenant_a, qn, D2, cart=2)
            await _ad(session, tenant_a, ad, D2, cart=10)
            await session.flush()

            rows = [r for r in await _trend(session, tenant_a, None) if r["goods_id"] == goods.id]
            all_cart = sum(int(r["paired_all_cart"]) for r in rows)
            ad_cart = sum(int(r["paired_ad_cart"]) for r in rows)
            assert (all_cart, ad_cart) == (17, 20)
            assert all_cart - ad_cart == -3
            assert sum(int(r["paired_days"]) for r in rows) == 2
        finally:
            tenant_id_ctx.reset(tok)

    async def test_pairing_is_per_channel(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """同一天普通千牛 + 直播万相台不配对；直播千牛 + 直播万相台配在直播行上。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            style, goods = await _goods(session, tenant_a, product_factory)
            qn_normal = await _link(session, tenant_a, style, goods, platform="千牛")
            qn_live = await _link(session, tenant_a, style, goods, platform="千牛", channel="直播")
            ad_live = await _link(
                session, tenant_a, style, goods, platform="万相台", channel="直播"
            )
            await _qn(session, tenant_a, qn_normal, D1, cart=20)
            await _ad(session, tenant_a, ad_live, D1, cart=3)
            await _qn(session, tenant_a, qn_live, D2, cart=7)
            await _ad(session, tenant_a, ad_live, D2, cart=5)
            await session.flush()

            rows = {
                (r["channel"], r["date"]): _paired(r)
                for r in await _trend(session, tenant_a, None)
                if r["goods_id"] == goods.id
            }
            assert rows == {
                ("普通", D1): (0, 0, 0),
                ("直播", D1): (0, 0, 0),
                ("直播", D2): (7, 5, 1),
            }
            # 单商品趋势合计渠道：只有直播 D2 那一对
            (week,) = await _trend(session, tenant_a, goods.id, granularity="week")
            assert _paired(week) == (7, 5, 1)
        finally:
            tenant_id_ctx.reset(tok)
