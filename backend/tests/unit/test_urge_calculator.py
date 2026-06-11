"""U04 urge_calculator 单元测试（FB8 守护）.

覆盖：
- BR-U04-30 7 个分支全覆盖
- get_today 时区正确（freezegun 边界日 23:59 UTC = 次日 07:59 GMT+8）
- 边界用例：scheduled == today → "重要催发"
"""

from __future__ import annotations

from datetime import date

import pytest
from freezegun import freeze_time

from app.modules.promotion.urge_calculator import (
    DEFAULT_TENANT_TZ,
    calculate_urge_status,
    get_today,
)


class TestCalculateUrgeStatus:
    """7 个分支覆盖（BR-U04-30）."""

    def _call(
        self,
        publish_status: str,
        scheduled: date | None,
        today: date,
    ) -> str:
        return calculate_urge_status(
            publish_status=publish_status,
            scheduled_publish_date=scheduled,
            today=today,
            urge_threshold_days=10,
            important_threshold_days=3,
        )

    def test_cancelled(self) -> None:
        assert (
            self._call("已取消", date(2026, 6, 1), date(2026, 5, 26))
            == "已取消"
        )

    def test_published(self) -> None:
        assert (
            self._call("已发布", date(2026, 6, 1), date(2026, 5, 26))
            == "已发布"
        )

    def test_deleted_branch(self) -> None:
        """publish_status 不在 {未发布, 异常} 集合 → '已删除'."""
        assert (
            self._call("已删除", None, date(2026, 5, 26)) == "已删除"
        )

    def test_unscheduled(self) -> None:
        assert self._call("未发布", None, date(2026, 5, 26)) == "未排期"

    def test_within_schedule(self) -> None:
        """scheduled - today > 10 天 → 档期内."""
        assert (
            self._call("未发布", date(2026, 6, 10), date(2026, 5, 26))
            == "档期内"
        )

    def test_urge(self) -> None:
        """3 < diff <= 10 → 催发."""
        assert (
            self._call("未发布", date(2026, 6, 4), date(2026, 5, 26))
            == "催发"
        )

    def test_important_urge(self) -> None:
        """0 <= diff <= 3 → 重要催发."""
        assert (
            self._call("未发布", date(2026, 5, 27), date(2026, 5, 26))
            == "重要催发"
        )

    def test_important_urge_boundary(self) -> None:
        """边界用例：scheduled == today → 重要催发（diff == 0）."""
        assert (
            self._call("未发布", date(2026, 5, 26), date(2026, 5, 26))
            == "重要催发"
        )

    def test_overdue(self) -> None:
        """diff < 0 → 超时."""
        assert (
            self._call("未发布", date(2026, 5, 25), date(2026, 5, 26))
            == "超时"
        )

    def test_abnormal_uses_same_logic(self) -> None:
        """publish_status='异常' 也走日期分支."""
        assert (
            self._call("异常", date(2026, 5, 27), date(2026, 5, 26))
            == "重要催发"
        )


class TestGetToday:
    """统一日期入口（FB8）."""

    def test_default_timezone_is_shanghai(self) -> None:
        assert str(DEFAULT_TENANT_TZ) == "Asia/Shanghai"

    @freeze_time("2026-05-26 23:59:00")  # UTC 23:59
    def test_get_today_at_utc_boundary_returns_next_day_in_shanghai(self) -> None:
        """UTC 23:59 = Asia/Shanghai 07:59 次日."""
        # freeze_time 默认设 UTC；get_today 转 Asia/Shanghai 应得 5/27
        result = get_today()
        assert result == date(2026, 5, 27)

    @freeze_time("2026-05-26 12:00:00")  # UTC 12:00 = Shanghai 20:00
    def test_get_today_normal_day(self) -> None:
        assert get_today() == date(2026, 5, 26)
