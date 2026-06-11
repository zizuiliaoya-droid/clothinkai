"""U05 双口径汇总性能测试（FB7 NFR 验证）.

SLA：10K settlement 双口径汇总 P95 ≤ 300ms（参考 NFR §3.1）。
冒烟级别（local + CI 跑），完整 perf 测在 staging 跑。

标记为 ``performance``，CI 默认跳过。
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.finance.repository import SettlementRepository
from app.modules.promotion.urge_calculator import get_today


@pytest.mark.performance
@pytest.mark.asyncio
async def test_daily_summary_as_of_smoke_perf(
    session: AsyncSession,
    tenant_a: Any,
    product_factory: Any,
    blogger_factory: Any,
    settlement_factory: Any,
) -> None:
    """口径 B（GROUP BY）2000 settlement 冒烟阈值 < 1s。"""
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        statuses = ["待核查", "待付款", "待财务付款", "已付款", "已驳回"]
        for i in range(2000):
            await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status=statuses[i % 5],
                total_amount=Decimal("100.00"),
            )
        await session.flush()

        repo = SettlementRepository(session)
        max_ms = 0.0
        for _ in range(5):
            start = time.perf_counter()
            await repo.daily_summary_as_of(
                tenant_id=tenant_a.id, date_value=get_today()
            )
            max_ms = max(max_ms, (time.perf_counter() - start) * 1000)
        assert max_ms < 1000, f"as_of summary perf: {max_ms:.0f}ms"
    finally:
        tenant_id_ctx.reset(token)


@pytest.mark.performance
@pytest.mark.asyncio
async def test_daily_summary_activity_smoke_perf(
    session: AsyncSession,
    tenant_a: Any,
    product_factory: Any,
    blogger_factory: Any,
    settlement_factory: Any,
) -> None:
    """口径 A（含 audit_log JOIN）2000 settlement 冒烟阈值 < 1.5s。"""
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        for i in range(2000):
            await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status="待核查",
                total_amount=Decimal("100.00"),
            )
        await session.flush()

        repo = SettlementRepository(session)
        max_ms = 0.0
        for _ in range(5):
            start = time.perf_counter()
            await repo.daily_summary_activity(
                tenant_id=tenant_a.id, date_value=get_today()
            )
            max_ms = max(max_ms, (time.perf_counter() - start) * 1000)
        # activity 含 audit JOIN，阈值放宽到 1.5s
        assert max_ms < 1500, f"activity summary perf: {max_ms:.0f}ms"
    finally:
        tenant_id_ctx.reset(token)
