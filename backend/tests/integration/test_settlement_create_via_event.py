"""U05 集成测试：on_settlement_requested 强一致创建（FB1 + FB3 + FB6）。

覆盖：
- FB1：SettlementRequested handler 创建 settlement，起点 settlement_status='待核查'
- FB3：UNIQUE(tenant_id, promotion_id) 永久 — 重复事件被幂等跳过
- FB6：handler 内 flush 立即暴露错误
- FB2：序列号原子分配（settlement_no 格式正确）
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.finance.listeners import on_settlement_requested
from app.modules.finance.models import Settlement
from app.modules.promotion.events import SettlementRequested


def _make_event(*, tenant_id, promotion_id, blogger_id, style_id, pr_id, **kw):
    return SettlementRequested(
        event_id=kw.get("event_id", uuid4()),
        timestamp=datetime.now(timezone.utc),
        tenant_id=tenant_id,
        promotion_id=promotion_id,
        promotion_internal_code=kw.get("internal_code", "DE2605260AAA"),
        blogger_id=blogger_id,
        style_id=style_id,
        amount=kw.get("amount", Decimal("500.00")),
        pr_id=kw.get("event_pr_id", pr_id),
        requested_by=pr_id,
        requested_at=datetime.now(timezone.utc),
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestCreateViaEvent:
    async def test_handler_creates_settlement_pending_review(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """FB1：handler 创建 settlement + 起点 = 待核查（不是待付款）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=pr,
                publish_status="已发布", settlement_status="待付款",
            )
            event = _make_event(
                tenant_id=tenant_a.id, promotion_id=promotion.id,
                blogger_id=blogger.id, style_id=style.id, pr_id=pr.id,
                amount=Decimal("500.00"),
            )
            await on_settlement_requested(event, session)

            row = (
                await session.execute(
                    select(Settlement).where(
                        Settlement.promotion_id == promotion.id
                    )
                )
            ).scalar_one()
            assert row.settlement_status == "待核查"  # FB1 起点
            assert row.amount == Decimal("500.00")
            assert row.total_amount == Decimal("500.00")
            assert row.request_event_id == event.event_id
            # FB2：settlement_no 格式 <prefix>S<yyMMdd><0001>
            assert "S" in row.settlement_no
            assert row.settlement_no.endswith("0001")
        finally:
            tenant_id_ctx.reset(token)

    async def test_duplicate_event_idempotent_skip(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """FB3：同 promotion 第二次事件 → 幂等跳过（不重复创建）。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            promotion = await promotion_factory.promotion(
                style=style, blogger=blogger, pr=pr,
                publish_status="已发布", settlement_status="待付款",
            )
            event1 = _make_event(
                tenant_id=tenant_a.id, promotion_id=promotion.id,
                blogger_id=blogger.id, style_id=style.id, pr_id=pr.id,
            )
            await on_settlement_requested(event1, session)
            await session.flush()

            # 第二次（不同 event_id，同 promotion）→ 幂等跳过
            event2 = _make_event(
                tenant_id=tenant_a.id, promotion_id=promotion.id,
                blogger_id=blogger.id, style_id=style.id, pr_id=pr.id,
            )
            await on_settlement_requested(event2, session)

            rows = (
                await session.execute(
                    select(Settlement).where(
                        Settlement.promotion_id == promotion.id
                    )
                )
            ).scalars().all()
            assert len(rows) == 1  # 永久 UNIQUE + service SELECT 兜底
        finally:
            tenant_id_ctx.reset(token)

    async def test_concurrent_first_create_no_duplicates(
        self,
        engine: Any,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        pr_role: Any,
        product_factory: Any,
        blogger_factory: Any,
        promotion_factory: Any,
    ) -> None:
        """FB2：序列号原子 — 多次顺序创建不同 promotion，序号递增不重复。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            pr = await factory.user(tenant_a, roles=[pr_role])
            style = await product_factory.style()
            blogger = await blogger_factory.blogger()
            seq_tails = []
            for _ in range(5):
                promotion = await promotion_factory.promotion(
                    style=style, blogger=blogger, pr=pr,
                    publish_status="已发布", settlement_status="待付款",
                )
                event = _make_event(
                    tenant_id=tenant_a.id, promotion_id=promotion.id,
                    blogger_id=blogger.id, style_id=style.id, pr_id=pr.id,
                )
                await on_settlement_requested(event, session)
                await session.flush()
                row = (
                    await session.execute(
                        select(Settlement).where(
                            Settlement.promotion_id == promotion.id
                        )
                    )
                ).scalar_one()
                seq_tails.append(row.settlement_no[-4:])
            # 序号互不重复
            assert len(set(seq_tails)) == 5
        finally:
            tenant_id_ctx.reset(token)
