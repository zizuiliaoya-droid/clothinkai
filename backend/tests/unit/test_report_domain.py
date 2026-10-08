"""U08 report.domain 单元测试（TimeRange 解析 + 时间分桶 + level 着色）。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.modules.promotion.urge_calculator import get_today
from app.modules.report.domain import (
    bucket_start,
    level_overdue_rate,
    level_publish_rate,
    resolve_time_range,
)
from app.modules.report.exceptions import (
    ReportInvalidTimePresetError,
    ReportInvalidTimeRangeError,
)


def test_last_7d():
    today = get_today()
    f, t = resolve_time_range("last_7d")
    assert t == today
    assert f == today - timedelta(days=6)


def test_last_30d():
    f, t = resolve_time_range("last_30d")
    assert (t - f).days == 29


def test_this_month():
    today = get_today()
    f, t = resolve_time_range("this_month")
    assert f == today.replace(day=1)
    assert t == today


def test_last_month():
    f, t = resolve_time_range("last_month")
    assert f.day == 1
    assert f.replace(day=1) <= t
    # 上月末的下一天是本月 1 日
    assert (t + timedelta(days=1)).day == 1


def test_custom_valid():
    f, t = resolve_time_range("custom", date(2026, 6, 1), date(2026, 6, 10))
    assert f == date(2026, 6, 1) and t == date(2026, 6, 10)


def test_custom_missing_dates():
    with pytest.raises(ReportInvalidTimeRangeError):
        resolve_time_range("custom", None, date(2026, 6, 10))


def test_custom_from_after_to():
    with pytest.raises(ReportInvalidTimeRangeError):
        resolve_time_range("custom", date(2026, 6, 10), date(2026, 6, 1))


def test_custom_span_too_large():
    with pytest.raises(ReportInvalidTimeRangeError):
        resolve_time_range("custom", date(2025, 1, 1), date(2026, 12, 31))


def test_invalid_preset():
    with pytest.raises(ReportInvalidTimePresetError):
        resolve_time_range("yesterday")


@pytest.mark.parametrize(
    ("value", "granularity", "expected"),
    [
        (date(2026, 3, 1), "day", date(2026, 3, 1)),
        # 03-01 是周日：所在 ISO 周从 02-23（周一）开始，跨了月
        (date(2026, 3, 1), "week", date(2026, 2, 23)),
        (date(2026, 3, 31), "week", date(2026, 3, 30)),
        (date(2026, 3, 31), "month", date(2026, 3, 1)),
        (date(2026, 3, 31), "year", date(2026, 1, 1)),
    ],
)
def test_bucket_start(value: date, granularity: str, expected: date) -> None:
    assert bucket_start(value, granularity) == expected


def test_bucket_start_rejects_unknown_granularity():
    with pytest.raises(ValueError, match="hour"):
        bucket_start(date(2026, 3, 1), "hour")


def test_level_publish_rate():
    assert level_publish_rate(Decimal("0.9")) == "green"
    assert level_publish_rate(Decimal("0.6")) == "yellow"
    assert level_publish_rate(Decimal("0.2")) == "red"
    assert level_publish_rate(None) is None


def test_level_overdue_rate():
    assert level_overdue_rate(Decimal("0.05")) == "green"
    assert level_overdue_rate(Decimal("0.2")) == "yellow"
    assert level_overdue_rate(Decimal("0.5")) == "red"
    assert level_overdue_rate(None) is None
