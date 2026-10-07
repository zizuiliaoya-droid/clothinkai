"""8a-6 AC 48：写死来源的重复行为不变（设计 §4.1 的第二道护栏；第一道是声明测试）。

| 来源 | 声明 | 这里验证的实际行为 | 另有覆盖 |
|---|---|---|---|
| manual_settlement | REJECT | 撞已有结算单 → 行 failed「该推广已有结算单」，不进冲突 | test_import_settlement |
| manual_promotion | APPEND | 同一行导两次 → 两条推广单 | test_import_promotion |
| qianniu | OVERWRITE | 同平台 ID + 日期再导 → 覆盖成最新值 | test_qianniu_refund_cart |
| manual_tao_order / manual_brush_order | APPEND | 同一行导两次 → 两条 | test_order_adjustment |
| huitun | OVERWRITE | 同小红书 ID 再导 → 画像覆盖 | test_crawler_flow |

这五类来源的 adapter 都不实现 ``ContextAwareImportAdapter``：规则框架不碰它们（只改声明不会改行为）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.core.tenancy import tenant_id_ctx
from app.modules.finance.order_adjustment_models import OrderAdjustment
from app.modules.importer.adapter import ContextAwareImportAdapter
from app.modules.importer.adapters.huitun import HuitunImportAdapter
from app.modules.importer.adapters.order_adjustment import OrderAdjustmentImportAdapter
from app.modules.importer.adapters.promotion import PromotionImportAdapter
from app.modules.importer.adapters.qianniu import QianniuImportAdapter
from app.modules.importer.adapters.settlement import SettlementImportAdapter
from app.modules.importer.adapters.wanxiangtai import WanxiangtaiImportAdapter
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.promotion.models import Promotion
from app.tasks.import_tasks import _run_import_batch
from tests.integration.test_import_settlement import _cleanup as _settlement_cleanup
from tests.integration.test_import_settlement import _seed as _settlement_seed
from tests.integration.test_qianniu_refund_cart import _export_row, _goods_with_link, _import


@pytest.mark.unit
def test_fixed_sources_not_context_aware() -> None:
    adapters = [
        SettlementImportAdapter(),
        PromotionImportAdapter(),
        QianniuImportAdapter(),
        WanxiangtaiImportAdapter(),
        OrderAdjustmentImportAdapter("manual_tao_order", "拍单"),
        OrderAdjustmentImportAdapter("manual_brush_order", "刷单"),
        HuitunImportAdapter(),
    ]
    for adapter in adapters:
        assert not isinstance(adapter, ContextAwareImportAdapter), adapter.source


@pytest.mark.integration
@pytest.mark.asyncio
class TestSettlementReject:
    async def test_existing_settlement_row_fails_without_conflict(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        saved = dict(ImportAdapterRegistry._adapters)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(SettlementImportAdapter())

        suffix = uuid4().hex[:8]
        batch_id = uuid4()
        _tenant_id, _promo1, promo2 = await _settlement_seed(Maker, suffix, batch_id)
        csv_bytes = (
            "推广编号,结算日期,金额,总金额,付款金额,结算状态\n"
            f"{promo2},2026-06-01,500.00,500.00,,待核查\n"
        ).encode()
        import app.core.attachment as att_mod

        monkeypatch.setattr(att_mod.attachment_service, "get_object_bytes", lambda b, k: csv_bytes)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "failed"
            async with Maker() as check:
                job = (
                    await check.execute(
                        text("SELECT status, error_detail FROM import_job WHERE batch_id = :b"),
                        {"b": batch_id},
                    )
                ).one()
                assert job.status == "failed"
                assert "该推广已有结算单" in (job.error_detail or "")
                n = (
                    await check.execute(
                        text("SELECT count(*) FROM import_conflict WHERE batch_id = :b"),
                        {"b": batch_id},
                    )
                ).scalar_one()
                assert n == 0
                conflicted = (
                    await check.execute(
                        text("SELECT conflicted FROM import_batch WHERE id = :b"), {"b": batch_id}
                    )
                ).scalar_one()
                assert conflicted == 0
        finally:
            ImportAdapterRegistry.clear()
            ImportAdapterRegistry._adapters.update(saved)
            await _settlement_cleanup(Maker, suffix, batch_id)


@pytest.mark.integration
@pytest.mark.asyncio
class TestAppendAndOverwriteSources:
    async def test_promotion_append(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        blogger_factory: Any,
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            adapter = PromotionImportAdapter()
            parsed = adapter.parse_row(
                {
                    "款式编码": style.style_code,
                    "小红书ID": blogger.xiaohongshu_id,
                    "报价金额": "500",
                    "合作日期": "2026-06-01",
                },
                None,
            )
            assert adapter.validate(parsed) == []
            first = await adapter.upsert(
                parsed, session=session, tenant_id=tenant_a.id, actor_id=None
            )
            second = await adapter.upsert(
                parsed, session=session, tenant_id=tenant_a.id, actor_id=None
            )
            assert first[1] is True and second[1] is True
            assert first[0] != second[0]
            n = (
                await session.execute(
                    select(func.count())
                    .select_from(Promotion)
                    .where(Promotion.blogger_id == blogger.id)
                )
            ).scalar_one()
            assert n == 2
        finally:
            tenant_id_ctx.reset(token)

    @pytest.mark.parametrize(
        ("source", "order_type"), [("manual_tao_order", "拍单"), ("manual_brush_order", "刷单")]
    )
    async def test_order_adjustment_append(
        self, session: AsyncSession, tenant_a: Any, source: str, order_type: str
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            adapter = OrderAdjustmentImportAdapter(source, order_type)
            order_no = f"NO{uuid4().hex[:8]}"
            parsed = {
                "order_no": order_no,
                "amount": Decimal("10.00"),
                "order_date": date(2026, 6, 1),
            }
            assert adapter.validate(parsed) == []
            ids = [
                (
                    await adapter.upsert(
                        parsed, session=session, tenant_id=tenant_a.id, actor_id=None
                    )
                )
                for _ in range(2)
            ]
            assert ids[0][0] != ids[1][0]
            n = (
                await session.execute(
                    select(func.count())
                    .select_from(OrderAdjustment)
                    .where(OrderAdjustment.order_no == order_no)
                )
            ).scalar_one()
            assert n == 2
        finally:
            tenant_id_ctx.reset(token)

    async def test_qianniu_overwrite(
        self, session: AsyncSession, tenant_a: Any, product_factory: Any
    ) -> None:
        from app.modules.collect.models import QianniuDaily

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pid = f"QN{uuid4().hex[:10]}"
            day = date(2026, 3, 5)
            await _goods_with_link(session, tenant_a, product_factory, pid)
            await _import(session, tenant_a, [_export_row(pid, day, 支付金额="100.00")])
            await _import(session, tenant_a, [_export_row(pid, day, 支付金额="200.00")])
            rows = (
                (
                    await session.execute(
                        select(QianniuDaily)
                        .where(QianniuDaily.platform_id_snapshot == pid)
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
            assert rows[0].pay_amount == Decimal("200.00")
        finally:
            tenant_id_ctx.reset(token)

    async def test_huitun_overwrite(
        self, session: AsyncSession, tenant_a: Any, blogger_factory: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            xhs = f"HT{uuid4().hex[:8]}"
            blogger = await blogger_factory.blogger(xiaohongshu_id=xhs)
            adapter = HuitunImportAdapter()
            for likes in ("80", "120"):
                parsed = adapter.parse_row(
                    {"小红书ID": xhs, "平均点赞": likes, "平均阅读": "2000"}, None
                )
                await adapter.upsert(parsed, session=session, tenant_id=tenant_a.id, actor_id=None)
            await session.refresh(blogger)
            assert blogger.audience_profile["note_stats"]["avg_likes"] == 120
        finally:
            tenant_id_ctx.reset(token)
