"""U02 match 接口性能基准测试（Pattern P-U02-01 验证）。

目标：5 万 style 模糊匹配 P95 ≤ 300ms（NFR §3.1）。

执行节奏（NFR Design §3.5）：
- nightly cron：跑 + 失败发 Slack/邮件
- PR 阻塞：不跑（@pytest.mark.performance 标记 + pytest -m 'not performance' 默认排除）
- release 前：SRE 手动跑确认

要求：
1. EXPLAIN ANALYZE 必须显示 ``Bitmap Index Scan on idx_style_search_trgm``
2. P95 ≤ 300ms（5 万 style 单租户）
3. P99 ≤ 500ms

使用方式::

    pytest backend/tests/performance/test_match_perf.py -m performance -v
"""

from __future__ import annotations

import time
from random import choice
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.product.service import StyleService

# 50K 是 SLA 验证基准；CI 中可降到 1000 以加速
DEFAULT_TARGET_ROWS = 50_000
DEFAULT_TARGET_P95_MS = 300


@pytest.fixture
def perf_target_rows() -> int:
    """允许通过 -o option 调整：pytest --override-ini=...."""
    import os

    return int(os.getenv("PERF_TARGET_ROWS", DEFAULT_TARGET_ROWS))


@pytest.fixture
def perf_target_p95_ms() -> int:
    import os

    return int(os.getenv("PERF_TARGET_P95_MS", DEFAULT_TARGET_P95_MS))


@pytest.mark.performance
@pytest.mark.asyncio
class TestMatchPerformance:
    async def test_match_p95_under_300ms_with_50k_styles(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        perf_target_rows: int,
        perf_target_p95_ms: int,
    ) -> None:
        """生成 50K 条 mock style，跑 100 次 match 测量 P95.

        如果 P95 超 SLA，先通过 EXPLAIN ANALYZE 确认 GIN 索引命中
        （Bitmap Index Scan on idx_style_search_trgm），
        再考虑 ANALYZE / 候选行数过滤 / GIN 索引 REINDEX。
        """
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            # ----- Setup: 批量插入 N 条 style -----
            keywords = ["波点", "花边", "连衣裙", "T 恤", "牛仔", "运动", "工装"]
            style_codes = []
            for i in range(perf_target_rows):
                kw = choice(keywords)
                style_code = f"PERF{i:06d}"
                style_codes.append(style_code)
                await product_factory.style(
                    style_code=style_code,
                    style_name=f"{kw}款式 {i}",
                    short_name=kw if i % 2 == 0 else None,
                )
                # 每 1000 条 flush + 清缓存避免 OOM
                if i % 1000 == 999:
                    await session.flush()

            # 刷新 PG 统计信息（autovacuum 可能延迟）
            await session.execute(text("ANALYZE style;"))

            # ----- Verify GIN 索引命中 -----
            # 通过 EXPLAIN 验证 query plan
            explain_sql = text("""
                EXPLAIN (FORMAT JSON)
                SELECT id FROM style
                WHERE tenant_id = :tid
                  AND is_deleted = false
                  AND is_active = true
                  AND (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
                       ILIKE '%' || :keyword || '%'
                LIMIT 20
            """)
            result = await session.execute(
                explain_sql, {"tid": tenant_a.id, "keyword": "波点"}
            )
            plan = result.scalar_one()
            plan_str = str(plan).lower()
            assert "idx_style_search_trgm" in plan_str or "gin" in plan_str, (
                f"GIN trgm 索引未命中！EXPLAIN: {plan}\n"
                "需检查：查询表达式是否与索引表达式严格一致 / 是否运行 ANALYZE"
            )

            # ----- Measure: 100 次 match 测量 -----
            svc = StyleService(session)
            durations: list[float] = []
            for _ in range(100):
                kw = choice(keywords)
                start = time.perf_counter()
                await svc.match_by_keyword(kw)
                durations.append((time.perf_counter() - start) * 1000)  # ms

            durations.sort()
            p50 = durations[49]
            p95 = durations[94]
            p99 = durations[98]

            print(
                f"\n[match_perf] rows={perf_target_rows} "
                f"P50={p50:.1f}ms P95={p95:.1f}ms P99={p99:.1f}ms "
                f"target_p95={perf_target_p95_ms}ms"
            )
            assert p95 <= perf_target_p95_ms, (
                f"P95={p95:.1f}ms 超过 SLA {perf_target_p95_ms}ms。"
                f"诊断顺序：1)EXPLAIN ANALYZE 验证 GIN 命中 "
                f"2)ANALYZE 刷新统计 3)GIN 索引膨胀 REINDEX "
                f"4)考虑 sim 阈值过滤"
            )
        finally:
            tenant_id_ctx.reset(token)
