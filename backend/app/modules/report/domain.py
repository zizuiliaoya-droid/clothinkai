"""U08 report 领域纯函数（TimeRange 解析 + level 着色）。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.modules.promotion.urge_calculator import get_today
from app.modules.report.exceptions import (
    ReportInvalidTimePresetError,
    ReportInvalidTimeRangeError,
)

_MAX_SPAN_DAYS = 366

VALID_PRESETS = frozenset(
    {"last_7d", "last_30d", "this_month", "last_month", "custom"}
)


def resolve_time_range(
    preset: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[date, date]:
    """解析时间筛选为 [date_from, date_to]（含端点，Asia/Shanghai，FB8）。

    EP09-S07：last_7d / last_30d / this_month / last_month / custom。
    custom 必须 date_from ≤ date_to 且跨度 ≤ 366 天。
    """
    today = get_today()
    if preset == "last_7d":
        return today - timedelta(days=6), today
    if preset == "last_30d":
        return today - timedelta(days=29), today
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "last_month":
        first_this = today.replace(day=1)
        prev_last = first_this - timedelta(days=1)
        return prev_last.replace(day=1), prev_last
    if preset == "custom":
        if date_from is None or date_to is None or date_from > date_to:
            raise ReportInvalidTimeRangeError()
        if (date_to - date_from).days > _MAX_SPAN_DAYS:
            raise ReportInvalidTimeRangeError()
        return date_from, date_to
    raise ReportInvalidTimePresetError()


def level_publish_rate(rate: Decimal | None) -> str | None:
    """发布率着色：≥0.8 绿 / ≥0.5 黄 / 否则红（BR-U08-32）；None → None。"""
    if rate is None:
        return None
    if rate >= Decimal("0.8"):
        return "green"
    if rate >= Decimal("0.5"):
        return "yellow"
    return "red"


def level_overdue_rate(rate: Decimal | None) -> str | None:
    """超时率着色：≤0.1 绿 / ≤0.3 黄 / 否则红（BR-U08-32）；None → None。"""
    if rate is None:
        return None
    if rate <= Decimal("0.1"):
        return "green"
    if rate <= Decimal("0.3"):
        return "yellow"
    return "red"


__all__ = [
    "VALID_PRESETS",
    "level_overdue_rate",
    "level_publish_rate",
    "resolve_time_range",
]
