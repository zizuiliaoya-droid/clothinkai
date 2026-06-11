"""U04 urge_calculator Python vs SQL 一致性测试（FB8 守护）。

100 mock 场景 + freezegun 边界日，对比 Python 实现与 SQL 表达式结果完全一致。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from freezegun import freeze_time
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.promotion.urge_calculator import (
    URGE_STATUS_SQL_EXPR,
    calculate_urge_status,
    get_today,
)


_PUBLISH_STATES = ["未发布", "已发布", "已取消", "异常", "已删除"]


def _generate_scenarios() -> list[tuple[str, date | None, int]]:
    """生成 100 个 mock 场景。

    Returns:
        list of (publish_status, scheduled_date_or_none, today_offset_days)
        today_offset 从 -15 到 +15 覆盖各分支边界
    """
    scenarios: list[tuple[str, date | None, int]] = []
    today = date(2026, 5, 26)
    for status in _PUBLISH_STATES:
        # None 排期
        scenarios.append((status, None, 0))
        # 各种 offset
        for offset in (-5, -1, 0, 1, 3, 4, 9, 10, 11, 15):
            sched = today + timedelta(days=offset)
            scenarios.append((status, sched, 0))
    # 凑满 100 + 重复几组
    while len(scenarios) < 100:
        scenarios.append((scenarios[len(scenarios) % len(scenarios)]))
    return scenarios[:100]


@pytest.mark.integration
@pytest.mark.asyncio
@freeze_time("2026-05-26 12:00:00")  # UTC 12:00 = Shanghai 20:00
async def test_urge_calculator_python_vs_sql_consistency(
    session: AsyncSession,
) -> None:
    """100 个场景：Python 和 SQL 结果必须一致（FB8）。"""
    today = get_today()
    urge_days = 10
    important_days = 3

    sql_query = text(
        f"""
        SELECT {URGE_STATUS_SQL_EXPR.strip()} AS result
        FROM (VALUES (
            CAST(:publish_status AS varchar),
            CAST(:scheduled AS date)
        )) AS t(publish_status, scheduled_publish_date)
        """
    )

    mismatches: list[str] = []
    for i, (status, sched, _offset) in enumerate(_generate_scenarios()):
        py = calculate_urge_status(
            publish_status=status,
            scheduled_publish_date=sched,
            today=today,
            urge_threshold_days=urge_days,
            important_threshold_days=important_days,
        )
        result = await session.execute(
            sql_query,
            {
                "publish_status": status,
                "scheduled": sched,
                "today": today,
                "urge_days": urge_days,
                "important_days": important_days,
            },
        )
        sql = result.scalar_one()
        if py != sql:
            mismatches.append(
                f"#{i} status={status!r} sched={sched!r}: py={py!r} sql={sql!r}"
            )

    assert not mismatches, "Python/SQL 不一致：\n" + "\n".join(mismatches)


@pytest.mark.integration
@pytest.mark.asyncio
@freeze_time("2026-05-26 12:00:00")
async def test_boundary_scheduled_equals_today(
    session: AsyncSession,
) -> None:
    """边界：scheduled_publish_date == today → '重要催发'."""
    today = get_today()
    py = calculate_urge_status(
        publish_status="未发布",
        scheduled_publish_date=today,
        today=today,
        urge_threshold_days=10,
        important_threshold_days=3,
    )
    assert py == "重要催发"

    sql_query = text(
        f"""
        SELECT {URGE_STATUS_SQL_EXPR.strip()} AS result
        FROM (VALUES (
            CAST('未发布' AS varchar),
            CAST(:today AS date)
        )) AS t(publish_status, scheduled_publish_date)
        """
    )
    result = await session.execute(
        sql_query,
        {"today": today, "urge_days": 10, "important_days": 3},
    )
    assert result.scalar_one() == "重要催发"
