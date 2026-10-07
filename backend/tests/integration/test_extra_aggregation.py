"""7a-6 extra 列聚合：真实导出格式 → 导入 → 店铺数据（按日 / 按周）、投产报表与导出。

生产 09-17 店铺页「商品详情页跳出率」显示 6584.66 —— 171 个商品的跳出率相加。

这里**不手写任何日报行**（同 test_qianniu_refund_cart.py）：从照两份模板抄的样本行
（``tests/unit/test_extra_metrics.py``）开始，走 adapter 的 parse_row → validate → upsert，
再看各报表读到的 extra。

场景：商品甲、乙各绑一个千牛链接，甲再绑一个万相台主体。千牛：D1 甲（样本第 1 行）、
乙（第 2 行），D2 甲（第 3 行）；万相台：甲 D1（第 1 行）、D2（第 2 行，收藏店铺数为 0、
「店铺收藏成本」是空的）。D1、D2 在同一个 ISO 周。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.importer.adapters.qianniu import QianniuImportAdapter
from app.modules.importer.adapters.wanxiangtai import WanxiangtaiImportAdapter
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.advanced_schemas import StoreDailyManualUpdate
from app.modules.report.domain import bucket_start
from app.modules.report.export_service import ReportExportService
from app.modules.report.production_service import ProductionService
from app.modules.report.store_daily_service import StoreDailyService
from tests.unit.test_extra_metrics import qianniu_row, wanxiangtai_row

pytestmark = pytest.mark.asyncio

D1, D2 = date(2026, 6, 15), date(2026, 6, 16)  # 周一、周二
_STORE_FIXED = 7  # 店铺导出的固定列：日期 + 6 个指标
_PRODUCTION_FIXED = 15  # 投产导出的固定列：编码、简称、名称、含款号 + 11 个指标


async def _goods(
    session: AsyncSession,
    tenant: Any,
    product_factory: Any,
    *,
    qianniu_id: str,
    wanxiangtai_id: str | None = None,
) -> GoodsMain:
    style = await product_factory.style(tenant=tenant)
    goods = GoodsMain(
        tenant_id=tenant.id,
        goods_code=f"G{uuid4().hex[:8]}",
        goods_title=style.style_name,
    )
    session.add(goods)
    await session.flush()
    session.add(GoodsStyleItem(tenant_id=tenant.id, goods_main_id=goods.id, style_id=style.id))
    links = [("千牛", qianniu_id)]
    if wanxiangtai_id is not None:
        links.append(("万相台", wanxiangtai_id))
    for platform, platform_id in links:
        session.add(
            PlatformProduct(
                tenant_id=tenant.id,
                platform=platform,
                platform_id=platform_id,
                style_id=style.id,
                goods_main_id=goods.id,
            )
        )
    await session.flush()
    return goods


async def _import(
    session: AsyncSession, tenant: Any, adapter: Any, rows: list[dict[str, Any]]
) -> None:
    for raw in rows:
        parsed = adapter.parse_row(raw, None)
        assert adapter.validate(parsed) == []
        await adapter.upsert(parsed, session=session, tenant_id=tenant.id, actor_id=None)
    await session.flush()


async def _seed(session: AsyncSession, tenant: Any, product_factory: Any) -> dict[str, GoodsMain]:
    """按模块说明的场景导入；返回 {"a": 甲, "b": 乙}。调用方负责设置 tenant_id_ctx。"""
    qa, qb, wa = f"QN{uuid4().hex[:10]}", f"QN{uuid4().hex[:10]}", f"WX{uuid4().hex[:10]}"
    goods_a = await _goods(session, tenant, product_factory, qianniu_id=qa, wanxiangtai_id=wa)
    goods_b = await _goods(session, tenant, product_factory, qianniu_id=qb)

    def qn(index: int, platform_id: str, day: date) -> dict[str, Any]:
        return qianniu_row(
            index, 统计日期=day.isoformat(), 商品ID=platform_id, 主商品ID=platform_id
        )

    def wx(index: int, day: date) -> dict[str, Any]:
        return wanxiangtai_row(index, 日期=day.isoformat(), 主体ID=wa)

    await _import(
        session, tenant, QianniuImportAdapter(), [qn(0, qa, D1), qn(1, qb, D1), qn(2, qa, D2)]
    )
    await _import(session, tenant, WanxiangtaiImportAdapter(), [wx(0, D1), wx(1, D2)])
    return {"a": goods_a, "b": goods_b}


def _as_cell(value: str | None) -> Decimal | None:
    """页面上的 extra 值 → 导出单元格应有的值（导出把十进制字符串还原成数值）。"""
    return None if value is None else Decimal(value)


class TestStoreDaily:
    async def test_by_day(self, session: AsyncSession, tenant_a: Any, product_factory: Any) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory)
            rows = await StoreDailyService(session).get_dashboard(tenant_a.id, (D1, D2))
            by_day = {r.date: r for r in rows}
            assert set(by_day) == {D1, D2}

            # D1 同一天两个商品
            d1 = by_day[D1].extra
            assert d1["商品详情页跳出率"] is None  # 原来 78.15 + 74.09
            assert d1["平均停留时长"] is None
            assert d1["下单转化率"] == "0.63"  # (76 + 71) / (11269 + 12118) × 100，原来 1.26
            assert d1["访客平均价值"] == "1.18"  # 27548 / 23387，原来 2.37
            assert d1["支付金额"] == "27548.00"
            assert d1["月累计支付金额"] == "522741.00"  # 同一天跨商品可加
            assert "竞争力评分" not in d1  # 全是 "-"
            assert "商品ID" not in d1
            assert by_day[D1].visitors == 23387
            assert by_day[D1].pay_amount == Decimal("27548.00")

            # D2 只有甲一行：照原值
            d2 = by_day[D2].extra
            assert d2["商品详情页跳出率"] == "51.86"
            assert d2["下单转化率"] == "1.54"
            assert d2["月累计支付金额"] == "190560.00"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_by_week(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory)
            assert bucket_start(D1, "week") == bucket_start(D2, "week") == D1  # 场景前提

            service = StoreDailyService(session)
            days = await service.get_dashboard(tenant_a.id, (D1, D2))
            weeks = await service.get_dashboard(tenant_a.id, (D1, D2), granularity="week")
            assert [r.date for r in weeks] == [D1]
            week = weeks[0]
            # 跨了天：累计列不再相加
            assert week.extra["月累计支付金额"] is None
            assert week.extra["年累计支付金额"] is None
            # 三行一起重算：(76 + 71 + 55) / (11269 + 12118 + 3579) × 100
            assert week.extra["下单转化率"] == "0.75"
            assert week.extra["支付金额"] == "36393.00"
            assert week.extra["商品详情页跳出率"] is None
            # typed 列 = 日行之和
            assert week.visitors == sum(r.visitors for r in days) == 26966
            assert week.pay_amount == sum((r.pay_amount for r in days), Decimal(0))
            assert week.pay_amount == Decimal("36393.00")
            assert week.pay_orders == sum(r.pay_orders for r in days)
            assert week.ad_spend_total is None  # 没手填过：整周保持 None
        finally:
            tenant_id_ctx.reset(tok)

    async def test_week_sums_manual_spend(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        factory: Any,
        operations_role: Any,
    ) -> None:
        """手填的 3 个广告消耗按桶相加，整周都没填的那一项保持 None（原导出里的规则挪到 service）。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory)
            ops = await factory.user(tenant_a, roles=[operations_role])
            service = StoreDailyService(session)
            await service.upsert_manual(
                tenant_a.id, D1, StoreDailyManualUpdate(ad_spend_total=Decimal("150.00")), ops
            )
            await service.upsert_manual(
                tenant_a.id,
                D2,
                StoreDailyManualUpdate(
                    ad_spend_total=Decimal("50.00"), zhitongche_spend=Decimal("20.00")
                ),
                ops,
            )
            (week,) = await service.get_dashboard(tenant_a.id, (D1, D2), granularity="week")
            assert week.ad_spend_total == Decimal("200.00")
            assert week.zhitongche_spend == Decimal("20.00")
            assert week.yinli_spend is None
        finally:
            tenant_id_ctx.reset(tok)

    async def test_month_and_year_buckets(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory)
            service = StoreDailyService(session)
            months = await service.get_dashboard(tenant_a.id, (D1, D2), granularity="month")
            years = await service.get_dashboard(tenant_a.id, (D1, D2), granularity="year")
            assert [r.date for r in months] == [date(2026, 6, 1)]
            assert [r.date for r in years] == [date(2026, 1, 1)]
            assert months[0].extra == years[0].extra
            assert months[0].extra["下单转化率"] == "0.75"
            assert months[0].extra["月累计支付金额"] is None  # 跨天，与按周同一条规则
        finally:
            tenant_id_ctx.reset(tok)


class TestProduction:
    async def test_goods_across_days(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            goods = await _seed(session, tenant_a, product_factory)
            report = await ProductionService(session).get_report(
                tenant_a.id, (D1, D2), exclude_brushing=False
            )
            by_goods = {r.goods_id: r for r in report.items}
            a = by_goods[goods["a"].id].extra
            # 甲：千牛两天 + 万相台两天
            assert a["商品详情页跳出率"] is None
            assert a["下单转化率"] == "0.88"  # (76 + 55) / (11269 + 3579) × 100
            assert a["月累计支付金额"] is None  # 跨天不可加
            assert a["支付金额"] == "24062.00"
            assert a["点击率"] == "0.07013"  # (47 + 38) / (598 + 614)
            assert a["投入产出比"] == "70.23"  # (373.8 + 528) / (6.46 + 6.38)
            assert a["店铺收藏成本"] == "12.84"  # 第 2 天单元格是空的，仍按两天重算
            assert a["花费"] == "12.84"
            assert a["含预售投产比"] is None
            # 乙：千牛只有一行，照原值
            b = by_goods[goods["b"].id].extra
            assert b["商品详情页跳出率"] == "74.09"
            assert b["下单转化率"] == "0.59"
            assert b["月累计支付金额"] == "241800.00"
            assert "点击率" not in b
        finally:
            tenant_id_ctx.reset(tok)


class TestExportEqualsPage:
    @pytest.mark.parametrize("granularity", ["day", "week", "month", "year"])
    async def test_store_daily(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any, granularity: str
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed(session, tenant_a, product_factory)
            page = await StoreDailyService(session).get_dashboard(
                tenant_a.id, (D1, D2), granularity=granularity
            )
            headers, rows = await ReportExportService(session)._fetch_rows(
                tenant_a.id,
                "store-daily",
                (D1, D2),
                exclude_brushing=True,
                seasons=None,
                categories=None,
                granularity=granularity,
            )
            extra_keys = headers[_STORE_FIXED:]
            assert len(rows) == len(page)
            for exported, shown in zip(rows, page, strict=True):
                assert exported[:_STORE_FIXED] == [
                    shown.date,
                    shown.visitors,
                    shown.pay_amount,
                    shown.pay_orders,
                    shown.ad_spend_total,
                    shown.zhitongche_spend,
                    shown.yinli_spend,
                ]
                assert exported[_STORE_FIXED:] == [
                    _as_cell(shown.extra.get(key)) for key in extra_keys
                ]
            # 场景有效性：比的不是两边都空 —— 有重算值、有求和值，也有算不出来的 None
            if granularity != "day":
                assert len(rows) == 1
                cells = dict(zip(extra_keys, rows[0][_STORE_FIXED:], strict=True))
                assert cells["下单转化率"] == Decimal("0.75")
                assert cells["支付金额"] == Decimal("36393.00")
                assert cells["月累计支付金额"] is None
        finally:
            tenant_id_ctx.reset(tok)

    async def test_production(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            goods = await _seed(session, tenant_a, product_factory)
            report = await ProductionService(session).get_report(
                tenant_a.id, (D1, D2), exclude_brushing=True
            )
            headers, rows = await ReportExportService(session)._fetch_rows(
                tenant_a.id,
                "production",
                (D1, D2),
                exclude_brushing=True,
                seasons=None,
                categories=None,
                granularity="day",
            )
            extra_keys = headers[_PRODUCTION_FIXED:]
            exported = {row[0]: row[_PRODUCTION_FIXED:] for row in rows}
            assert set(exported) == {item.goods_code for item in report.items}
            for item in report.items:
                assert exported[item.goods_code] == [
                    _as_cell(item.extra.get(key)) for key in extra_keys
                ]
            cells = dict(zip(extra_keys, exported[goods["a"].goods_code], strict=True))
            assert cells["下单转化率"] == Decimal("0.88")
            assert cells["支付金额"] == Decimal("24062.00")
            assert cells["商品详情页跳出率"] is None
        finally:
            tenant_id_ctx.reset(tok)
