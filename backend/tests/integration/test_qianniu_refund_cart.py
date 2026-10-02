"""千牛日报的退款 / 加购：真实导出格式 → 导入 → 各报表（055 的回归测试）。

055 之前报表从 extra 按英文键 ``refund_amount`` / ``add_cart_count`` 取数，而导入写进
extra 的是生意参谋的中文原始表头 —— 生产上退款与加购一直是 0。之前的测试都是手工往
extra 塞英文键，和代码犯同一个错，所以一直是绿的。

这里**不手写任何 qianniu_daily 行**：从照着真实导出抄来的行（中文表头、千分位、"-" 占位）
开始，走 adapter 的 parse_row → validate → upsert，再看投产报表、趋势、BI 与汇总表各自
读到的数。
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
from app.modules.collect.models import QianniuDaily
from app.modules.importer.adapters.qianniu import QianniuImportAdapter
from app.modules.product.goods_models import GoodsMain, GoodsStyleItem
from app.modules.product.platform_product_models import PlatformProduct
from app.modules.report.bi_service import BiService
from app.modules.report.production_service import ProductionService
from app.modules.report.summary_read import SummaryReadRepository
from app.modules.report.summary_refresh_service import SummaryRefreshService

pytestmark = pytest.mark.asyncio

D1, D2 = date(2026, 3, 5), date(2026, 3, 6)


def _export_row(platform_id: str, day: date, **overrides: str) -> dict[str, str]:
    """一行生意参谋「商品_全部」导出。表头与取值格式照生产 2026-09-17 那份抄。"""
    row = {
        "统计日期": day.isoformat(),
        "商品ID": platform_id,
        "主商品ID": platform_id,
        "商品名称": "测试连衣裙",
        "货号": "T-001",
        "商品状态": "当前在线",
        "商品访客数": "1,024",
        "商品浏览量": "3,310",
        "商品加购人数": "215",
        "商品加购件数": "271",
        "支付买家数": "18",
        "支付件数": "21",
        "支付金额": "13,290.00",
        "成功退款金额": "5,652.00",
        "聚划算支付金额": "0.00",
    }
    row.update(overrides)
    return row


def _second_day(platform_id: str) -> dict[str, str]:
    """第二天：退款是占位符 "-"，加购列整个没有 —— 行不能失败，也不能被算成别的数。"""
    row = _export_row(platform_id, D2, 成功退款金额="-", 支付金额="2,000.00")
    del row["商品加购件数"]
    return row


async def _goods_with_link(
    session: AsyncSession, tenant: Any, product_factory: Any, platform_id: str
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
    session.add(
        PlatformProduct(
            tenant_id=tenant.id,
            platform="千牛",
            platform_id=platform_id,
            style_id=style.id,
            goods_main_id=goods.id,
        )
    )
    await session.flush()
    return goods


async def _import(session: AsyncSession, tenant: Any, rows: list[dict[str, str]]) -> None:
    adapter = QianniuImportAdapter()
    for raw in rows:
        parsed = adapter.parse_row(raw, None)
        assert adapter.validate(parsed) == []
        await adapter.upsert(parsed, session=session, tenant_id=tenant.id, actor_id=None)
    await session.flush()


class TestRefundAndAddCartFromRealExport:
    async def test_import_stores_typed_columns(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pid = f"QN{uuid4().hex[:10]}"
            await _goods_with_link(session, tenant_a, product_factory, pid)
            await _import(session, tenant_a, [_export_row(pid, D1), _second_day(pid)])

            rows = {
                r.date: r
                for r in (
                    await session.execute(
                        select(QianniuDaily).where(QianniuDaily.platform_id_snapshot == pid)
                    )
                ).scalars()
            }
            assert rows[D1].refund_amount == Decimal("5652.00")
            assert rows[D1].add_cart_count == 271
            # "-" 与缺列都是「没有这个数」，不是 0
            assert rows[D2].refund_amount is None
            assert rows[D2].add_cart_count is None
            # 原始表头照旧原样留档
            assert rows[D1].extra is not None
            assert rows[D1].extra["成功退款金额"] == "5,652.00"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_production_report_and_trend(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pid = f"QN{uuid4().hex[:10]}"
            goods = await _goods_with_link(session, tenant_a, product_factory, pid)
            await _import(session, tenant_a, [_export_row(pid, D1), _second_day(pid)])

            service = ProductionService(session)
            report = await service.get_report(
                tenant_a.id, (D1, D2), exclude_brushing=False, use_summary=False
            )
            row = next(r for r in report.items if r.goods_id == goods.id)
            assert row.pay_amount == Decimal("15290.00")
            assert row.refund_amount == Decimal("5652.00")
            assert row.confirmed_amount == Decimal("9638.00")
            assert row.add_cart_count == 271

            trend = await service.get_trend(tenant_a.id, goods.id, (D1, D2), granularity="day")
            by_day = {p.date: p for p in trend.points}
            assert by_day[D1].refund_amount == Decimal("5652.00")
            assert by_day[D2].refund_amount == Decimal("0")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_reimport_overwrites_refund_and_add_cart(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """同一天重导一次（ON CONFLICT 那条路）：新数覆盖旧数，不累加也不保留旧值。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pid = f"QN{uuid4().hex[:10]}"
            goods = await _goods_with_link(session, tenant_a, product_factory, pid)
            await _import(session, tenant_a, [_export_row(pid, D1)])
            await _import(
                session,
                tenant_a,
                [_export_row(pid, D1, 成功退款金额="6,000.00", 商品加购件数="300")],
            )

            report = await ProductionService(session).get_report(
                tenant_a.id, (D1, D1), exclude_brushing=False, use_summary=False
            )
            row = next(r for r in report.items if r.goods_id == goods.id)
            assert row.refund_amount == Decimal("6000.00")
            assert row.add_cart_count == 300
        finally:
            tenant_id_ctx.reset(tok)

    async def test_bi_store_summary_and_trend(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pid = f"QN{uuid4().hex[:10]}"
            await _goods_with_link(session, tenant_a, product_factory, pid)
            await _import(session, tenant_a, [_export_row(pid, D1), _second_day(pid)])

            dash = await BiService(session).get_dashboard(tenant_a.id, (D1, D2))
            assert dash.store_summary.sales_amount == Decimal("15290.00")
            assert dash.store_summary.refund_amount == Decimal("5652.00")
            by_day = {p.date: p for p in dash.trend}
            assert by_day[D1].refund_amount == Decimal("5652.00")
            assert by_day[D2].refund_amount == Decimal("0")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_summary_refresh_stores_what_live_reads(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        """汇总表刷新也要拿到 typed 列 —— 读汇总表的投产报表与实时逐字段一致。"""
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            pid = f"QN{uuid4().hex[:10]}"
            await _goods_with_link(session, tenant_a, product_factory, pid)
            await _import(session, tenant_a, [_export_row(pid, D1), _second_day(pid)])
            await SummaryRefreshService(session).refresh(
                tenant_id=tenant_a.id, date_from=D1, date_to=D2
            )
            fresh = await SummaryReadRepository(session).freshness(tenant_a.id, D1, D2)
            assert fresh.source == "summary"

            service = ProductionService(session)
            from_summary = await service.get_report(tenant_a.id, (D1, D2), exclude_brushing=False)
            live = await service.get_report(
                tenant_a.id, (D1, D2), exclude_brushing=False, use_summary=False
            )
            summary_rows = [r.model_dump(mode="json") for r in from_summary.items]
            live_rows = [r.model_dump(mode="json") for r in live.items]
            assert summary_rows == live_rows
            # 场景有效性：比的不是两边都是 0
            assert any(r["refund_amount"] == "5652.00" for r in summary_rows)
            assert any(r["add_cart_count"] == 271 for r in summary_rows)
        finally:
            tenant_id_ctx.reset(tok)
