"""U03 blogger 搜索性能基准测试（P-U03-01 验证）.

目标：3000 博主搜索 P95 ≤ 200ms（NFR §3.1）。

执行节奏（NFR Design §3.5）：
- nightly cron：跑 + 失败发 Slack/邮件
- PR 阻塞：不跑（@pytest.mark.performance 标记）
- release 前：SRE 手动跑

要求：
1. EXPLAIN ANALYZE 显示 GIN trgm 命中（Bitmap Index Scan）
2. 100 次搜索 P95 ≤ 200ms
"""

from __future__ import annotations

import time
from random import choice
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.blogger.repository import BloggerListFilters
from app.modules.blogger.service import BloggerService

DEFAULT_TARGET_ROWS = 3000
DEFAULT_TARGET_P95_MS = 200


@pytest.fixture
def perf_target_rows() -> int:
    import os

    return int(os.getenv("PERF_TARGET_ROWS", DEFAULT_TARGET_ROWS))


@pytest.fixture
def perf_target_p95_ms() -> int:
    import os

    return int(os.getenv("PERF_TARGET_P95_MS", DEFAULT_TARGET_P95_MS))


@pytest.mark.performance
@pytest.mark.asyncio
class TestBloggerSearchPerformance:
    async def test_search_p95_under_200ms_with_3k_records(
        self,
        session: AsyncSession,
        tenant_a: Any,
        factory: Any,
        admin_role: Any,
        blogger_factory: Any,
        perf_target_rows: int,
        perf_target_p95_ms: int,
    ) -> None:
        """生成 3000 条 mock blogger，跑 100 次搜索测量 P95."""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            keywords = ["时尚", "美食", "穿搭", "美妆", "数码", "运动", "母婴"]
            for i in range(perf_target_rows):
                kw = choice(keywords)
                await blogger_factory.blogger(
                    xiaohongshu_id=f"PERF{i:06d}",
                    nickname=f"{kw}博主 {i}",
                )
                if i % 500 == 499:
                    await session.flush()

            # 刷新统计信息
            await session.execute(text("ANALYZE blogger;"))

            # 验证 GIN 索引命中
            explain_sql = text("""
                EXPLAIN (FORMAT JSON)
                SELECT id FROM blogger
                WHERE tenant_id = :tid
                  AND is_deleted = false
                  AND is_active = true
                  AND (nickname ILIKE :pattern OR xiaohongshu_id ILIKE :pattern)
                LIMIT 20
            """)
            result = await session.execute(
                explain_sql,
                {"tid": tenant_a.id, "pattern": "%时尚%"},
            )
            plan = result.scalar_one()
            plan_str = str(plan).lower()
            assert "trgm" in plan_str or "gin" in plan_str, (
                f"GIN trgm 索引未命中！EXPLAIN: {plan}"
            )

            # 测量
            user = await factory.user(tenant_a, roles=[admin_role])
            svc = BloggerService(session)
            durations: list[float] = []
            for _ in range(100):
                kw = choice(keywords)
                start = time.perf_counter()
                await svc.list_bloggers(
                    filters=BloggerListFilters(keyword=kw),
                    page=1,
                    page_size=20,
                    user=user,
                )
                durations.append((time.perf_counter() - start) * 1000)

            durations.sort()
            p50 = durations[49]
            p95 = durations[94]
            p99 = durations[98]

            print(
                f"\n[blogger_search_perf] rows={perf_target_rows} "
                f"P50={p50:.1f}ms P95={p95:.1f}ms P99={p99:.1f}ms "
                f"target_p95={perf_target_p95_ms}ms"
            )
            assert p95 <= perf_target_p95_ms, (
                f"P95={p95:.1f}ms 超过 SLA {perf_target_p95_ms}ms"
            )
        finally:
            tenant_id_ctx.reset(token)
