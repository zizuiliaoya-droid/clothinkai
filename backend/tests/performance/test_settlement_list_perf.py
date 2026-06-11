"""U05 settlement 列表性能测试（NFR 验证）.

SLA：10K settlement 列表 P95 ≤ 200ms（参考 NFR §3.1）。
本测试是冒烟级别（local + CI 跑），完整 perf 测在 staging 跑。

标记为 ``performance``，CI 默认跳过（``-m "not performance"``）。
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.finance.repository import (
    SettlementListFilters,
    SettlementRepository,
)


@pytest.mark.performance
@pytest.mark.asyncio
async def test_list_smoke_perf(
    session: AsyncSession,
    tenant_a: Any,
    product_factory: Any,
    blogger_factory: Any,
    settlement_factory: Any,
) -> None:
    """1000 settlement 列表 P95 应 < 1s（冒烟阈值；staging 应 < 200ms）."""
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        statuses = ["待核查", "待付款", "待财务付款", "已付款", "已驳回"]
        for i in range(1000):
            await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_status=statuses[i % 5],
                total_amount=Decimal("100.00") + Decimal(i),
            )
        await session.flush()

        repo = SettlementRepository(session)
        filters = SettlementListFilters()

        max_ms = 0.0
        for _ in range(5):
            start = time.perf_counter()
            rows, total = await repo.list_with_filters(
                tenant_id=tenant_a.id,
                filters=filters,
                page=1,
                page_size=20,
            )
            max_ms = max(max_ms, (time.perf_counter() - start) * 1000)

        assert total == 1000
        assert len(rows) == 20
        assert max_ms < 1000, f"List perf: {max_ms:.0f}ms"
    finally:
        tenant_id_ctx.reset(token)


@pytest.mark.performance
@pytest.mark.asyncio
async def test_list_keyword_search_smoke_perf(
    session: AsyncSession,
    tenant_a: Any,
    product_factory: Any,
    blogger_factory: Any,
    settlement_factory: Any,
) -> None:
    """settlement_no 关键字搜索（GIN trgm）冒烟阈值。"""
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()
        for i in range(500):
            await settlement_factory.settlement(
                style=style, blogger=blogger,
                settlement_no=f"DES260526{i:04d}",
                total_amount=Decimal("100.00"),
            )
        await session.flush()

        repo = SettlementRepository(session)
        filters = SettlementListFilters(keyword="2605260042")

        start = time.perf_counter()
        rows, total = await repo.list_with_filters(
            tenant_id=tenant_a.id, filters=filters, page=1, page_size=20
        )
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert total >= 1
        assert elapsed_ms < 1000, f"Keyword search perf: {elapsed_ms:.0f}ms"
    finally:
        tenant_id_ctx.reset(token)
