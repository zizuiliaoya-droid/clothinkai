"""U04 promotion 列表性能测试（NFR 验证）.

SLA：10K promotion + CTE 列表 P95 ≤ 300ms（参考 NFR §3.1）。
本测试是冒烟级别（local + CI 跑），完整 perf 测在 staging 跑。

标记为 ``performance``，CI 默认跳过（``-m "not performance"``）。
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.promotion.repository import (
    PromotionListFilters,
    PromotionRepository,
)
from app.modules.promotion.urge_calculator import get_today


@pytest.mark.performance
@pytest.mark.asyncio
async def test_list_with_cte_smoke_perf(
    session: AsyncSession,
    tenant_a: Any,
    factory: Any,
    admin_role: Any,
    product_factory: Any,
    blogger_factory: Any,
    promotion_factory: Any,
) -> None:
    """1000 推广 + CTE 列表 P95 应 < 1s（冒烟阈值；完整 perf 测在 staging 跑）."""
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        await factory.user(tenant_a, roles=[admin_role])
        style = await product_factory.style()
        blogger = await blogger_factory.blogger()

        # 准备 1000 条数据
        base_date = date(2026, 1, 1)
        for i in range(1000):
            await promotion_factory.promotion(
                style=style,
                blogger=blogger,
                cooperation_date=base_date + timedelta(days=i % 100),
                quote_amount=Decimal("100.00") + Decimal(i),
                internal_code=f"DE2601010{i:04d}",
            )

        repo = PromotionRepository(session)
        filters = PromotionListFilters()

        # 测 5 次取最大
        max_ms = 0.0
        for _ in range(5):
            start = time.perf_counter()
            rows, total = await repo.list_with_cte(
                tenant_id=tenant_a.id,
                filters=filters,
                page=1,
                page_size=20,
                today=get_today(),
                urge_threshold_days=10,
                important_threshold_days=3,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            max_ms = max(max_ms, elapsed_ms)

        assert total == 1000
        assert len(rows) == 20
        # 冒烟阈值 1000ms（CI 容器较慢；staging 真实环境应 < 300ms）
        assert max_ms < 1000, f"List perf: {max_ms:.0f}ms"
    finally:
        tenant_id_ctx.reset(token)
