"""店铺日汇总刷新区间要扩到完整的周桶与月桶（纯规则，不连库）。

周/月汇总从日汇总二次聚合。只刷 ``[date_from, date_to]`` 的日汇总的话，与区间同属
一个周/月、但落在区间外的那几天可能从没刷过，周/月那一行就会缺掉它们。

定时任务窗口是「今天往前 31 天」，下沿几乎总落在某个月中间，所以**每次**都会撞上。
生产的第一次刷新窗口就是 09-02 ~ 10-02：不扩展，9 月这一行永远缺 09-01。
"""

from __future__ import annotations

from datetime import date

from app.modules.report.summary_refresh_service import shop_daily_span


class TestShopDailySpan:
    def test_production_hourly_window(self) -> None:
        """生产第一次刷新的真实窗口。"""
        lo, hi = shop_daily_span(date(2026, 9, 2), date(2026, 10, 2))
        assert lo == date(2026, 8, 31)  # min(09-02 所在周的周一 08-31, 月初 09-01)
        assert hi == date(2026, 10, 31)  # max(10-02 所在周的周日 10-04, 月末 10-31)

    def test_range_inside_one_month(self) -> None:
        lo, hi = shop_daily_span(date(2026, 3, 10), date(2026, 3, 12))
        assert lo == date(2026, 3, 1)  # 月初早于周一（03-09）
        assert hi == date(2026, 3, 31)  # 月末晚于周日（03-15）

    def test_week_crossing_month_end(self) -> None:
        """03-31 是周二，它所在的周到 04-05 —— 上沿要跟着周走，否则那一周缺 4 月那几天。"""
        lo, hi = shop_daily_span(date(2026, 3, 1), date(2026, 3, 31))
        assert lo == date(2026, 2, 23)  # 03-01 是周日，所在周从 02-23 开始
        assert hi == date(2026, 4, 5)

    def test_year_boundary(self) -> None:
        lo, hi = shop_daily_span(date(2026, 12, 30), date(2027, 1, 2))
        assert lo == date(2026, 12, 1)
        assert hi == date(2027, 1, 31)

    def test_single_day(self) -> None:
        """单日刷新也要扩到整周整月 —— 手动刷新某一天是常见操作。"""
        lo, hi = shop_daily_span(date(2026, 3, 18), date(2026, 3, 18))
        assert lo == date(2026, 3, 1)
        assert hi == date(2026, 3, 31)

    def test_span_always_contains_input(self) -> None:
        """扩展只能变大，不能把原区间切掉。"""
        for lo_in, hi_in in (
            (date(2026, 1, 1), date(2026, 1, 1)),
            (date(2026, 2, 27), date(2026, 3, 2)),
            (date(2026, 6, 15), date(2026, 8, 20)),
        ):
            lo, hi = shop_daily_span(lo_in, hi_in)
            assert lo <= lo_in and hi >= hi_in
