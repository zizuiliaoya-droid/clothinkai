"""店铺日 / 周 / 月分桶纯函数（7e R4，设计 2.4：逐天套默 13 与仅退款，再按桶求和）。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.modules.report.store_buckets import bucket_store_rows, total_store_row

# 2026-09-09 是周三，所在 ISO 周 = 09-07（一）~ 09-13（日）
WED = date(2026, 9, 9)
FRI = WED + timedelta(days=2)


def _day(d: date, **kw: Any) -> dict[str, Any]:
    """与 StoreDailyRepository.aggregate / SummaryReadRepository.store_daily 同一套键。"""
    row: dict[str, Any] = {
        "date": d,
        "visitors": 0,
        "pay_amount": Decimal("0"),
        "pay_orders": 0,
        "refund_amount": Decimal("0"),
        "brushing_amount": Decimal("0"),
        "ad_spend": Decimal("0"),
        "promo_cost": Decimal("0"),
        "live_pay_amount": Decimal("0"),
        "live_ad_spend": Decimal("0"),
        "ad_spend_total": None,
        "zhitongche_spend": None,
        "yinli_spend": None,
        "refund_only_amount": None,
    }
    for key, value in kw.items():
        assert key in row, key
        row[key] = Decimal(value) if isinstance(value, str) else value
    return row


def test_formulas_on_one_day() -> None:
    [row] = bucket_store_rows(
        [
            _day(
                WED,
                visitors=30,
                pay_amount="1000",
                pay_orders=12,
                refund_amount="100",
                brushing_amount="50",
                ad_spend="60",
                promo_cost="40",
                live_pay_amount="300",
                live_ad_spend="7",
            )
        ],
        "day",
        WED,
        WED,
    )
    assert row.date_from == row.date_to == WED
    assert (row.visitors, row.pay_orders, row.day_count) == (30, 12, 1)
    assert row.actual_sales == Decimal("850.00")
    assert row.internal_spend == Decimal("60.00")
    assert row.total_spend == Decimal("100.00")
    assert row.total_spend_ratio == Decimal("0.1000")  # ÷ 支付总额
    assert row.internal_ratio == Decimal("0.0706")  # 60 / 850
    assert row.external_ratio == Decimal("0.0471")  # 40 / 850
    assert row.net_spend_ratio == Decimal("0.1176")  # 100 / 850
    assert row.net_roi == Decimal("8.5000")
    assert row.roi_ex_brushing == Decimal("9.5000")  # (1000 − 50) / 100
    # 「其中直播」只展示，不进公式
    assert (row.live_pay_amount, row.live_ad_spend) == (Decimal("300.00"), Decimal("7.00"))
    assert row.ad_spend_total is None and row.zhitongche_spend is None
    assert row.yinli_spend is None
    assert row.extra == {}


def test_manual_zero_overrides_wanxiangtai() -> None:
    """手填 0 也算填了：当天站内 = 0，不回落到万相台。"""
    [row] = bucket_store_rows(
        [_day(WED, ad_spend="100", zhitongche_spend="0", promo_cost="5")], "day", WED, WED
    )
    assert row.internal_spend == Decimal("0.00")
    assert row.zhitongche_spend == Decimal("0.00")
    assert row.ad_spend == Decimal("100.00")
    assert row.total_spend == Decimal("5.00")


def test_week_applies_manual_rule_per_day() -> None:
    """周三只填了直通车 40（当天万相台 50），周五只有万相台 80 → 周站内 = 40 + 80。"""
    [row] = bucket_store_rows(
        [
            _day(WED, ad_spend="50", zhitongche_spend="40"),
            _day(FRI, ad_spend="80"),
        ],
        "week",
        WED - timedelta(days=2),
        WED + timedelta(days=4),
    )
    assert row.internal_spend == Decimal("120.00")
    assert row.ad_spend == Decimal("130.00")
    assert row.zhitongche_spend == Decimal("40.00")
    assert row.ad_spend_total is None and row.yinli_spend is None
    assert row.day_count == 2


def test_refund_only_counts_recorded_days_only() -> None:
    """仅退款录了 1 / 3 天：比率分子分母只取那一天。"""
    [row] = bucket_store_rows(
        [
            _day(WED, pay_amount="100"),
            _day(WED + timedelta(days=1), pay_amount="200", refund_only_amount="20"),
            _day(FRI, pay_amount="300"),
        ],
        "week",
        WED,
        FRI,
    )
    assert row.refund_only_amount == Decimal("20.00")
    assert row.refund_only_rate == Decimal("0.1000")  # 20 / 200，不是 20 / 600
    assert (row.refund_only_days, row.day_count) == (1, 3)


def test_recorded_zero_refund_only_counts_as_recorded() -> None:
    [row] = bucket_store_rows(
        [_day(WED, pay_amount="100", refund_only_amount="0")], "day", WED, WED
    )
    assert row.refund_only_amount == Decimal("0.00")
    assert row.refund_only_rate == Decimal("0.0000")
    assert row.refund_only_days == 1


def test_bucket_without_refund_only_is_none() -> None:
    [row] = bucket_store_rows(
        [_day(WED, pay_amount="100"), _day(FRI, pay_amount="50")], "week", WED, FRI
    )
    assert row.refund_only_amount is None
    assert row.refund_only_rate is None
    assert (row.refund_only_days, row.day_count) == (0, 2)


def test_week_rows_carry_actual_range() -> None:
    """区间从周三开始：首行 date_from = 周三；后一周整周；末行截到请求止日。"""
    date_to = WED + timedelta(days=9)  # 下周五
    rows = bucket_store_rows(
        [_day(WED), _day(WED + timedelta(days=6)), _day(date_to)], "week", WED, date_to
    )
    assert [(r.date_from, r.date_to, r.day_count) for r in rows] == [
        (WED, WED + timedelta(days=4), 1),
        (WED + timedelta(days=5), date_to, 2),
    ]


def test_month_splits_at_month_boundary() -> None:
    date_from, date_to = date(2026, 8, 28), date(2026, 9, 3)
    day_rows = [_day(date(2026, 8, 29), pay_amount="10"), _day(date(2026, 9, 2), pay_amount="20")]
    months = bucket_store_rows(day_rows, "month", date_from, date_to)
    assert [(r.date_from, r.date_to, r.pay_amount) for r in months] == [
        (date(2026, 8, 28), date(2026, 8, 31), Decimal("10.00")),
        (date(2026, 9, 1), date(2026, 9, 3), Decimal("20.00")),
    ]
    # 周按 ISO 周 ∩ 区间：08-29 在 08-24 那周，09-02 在 08-31 那周（跨月的周不切）
    weeks = bucket_store_rows(day_rows, "week", date_from, date_to)
    assert [(r.date_from, r.date_to) for r in weeks] == [
        (date(2026, 8, 28), date(2026, 8, 30)),
        (date(2026, 8, 31), date(2026, 9, 3)),
    ]


def test_sum_before_rounding() -> None:
    """可加列逐天保留精确值，求和之后才取两位小数。"""
    [row] = bucket_store_rows(
        [
            _day(WED, refund_amount="0.005", pay_amount="1"),
            _day(FRI, refund_amount="0.005", pay_amount="1"),
        ],
        "week",
        WED,
        FRI,
    )
    assert row.refund_amount == Decimal("0.01")
    assert row.actual_sales == Decimal("1.99")


def test_ratios_none_when_denominator_zero_or_negative() -> None:
    [row] = bucket_store_rows(
        [_day(WED, pay_amount="0", refund_amount="30", ad_spend="0")], "day", WED, WED
    )
    assert row.actual_sales == Decimal("-30.00")
    assert row.total_spend_ratio is None  # 支付 0
    assert row.internal_ratio is None and row.external_ratio is None  # 实际销售额为负
    assert row.net_spend_ratio is None
    assert row.net_roi is None and row.roi_ex_brushing is None  # 推广总额 0


def test_rows_sorted_and_empty() -> None:
    rows = bucket_store_rows([_day(FRI), _day(WED)], "day", WED, FRI)
    assert [r.date_from for r in rows] == [WED, FRI]
    assert bucket_store_rows([], "month", WED, FRI) == []


def test_unsupported_granularity() -> None:
    with pytest.raises(ValueError):
        bucket_store_rows([], "quarter", WED, FRI)


def test_year_bucket_clipped_to_range() -> None:
    """year（BI 趋势用）：自然年 ∩ 区间，跨年分两桶。"""
    date_from, date_to = date(2025, 12, 30), date(2026, 1, 2)
    rows = bucket_store_rows(
        [
            _day(date(2025, 12, 30), pay_amount="10"),
            _day(date(2025, 12, 31), pay_amount="5"),
            _day(date(2026, 1, 2), pay_amount="20"),
        ],
        "year",
        date_from,
        date_to,
    )
    assert [(r.date_from, r.date_to, r.pay_amount, r.day_count) for r in rows] == [
        (date(2025, 12, 30), date(2025, 12, 31), Decimal("15.00"), 2),
        (date(2026, 1, 1), date(2026, 1, 2), Decimal("20.00"), 1),
    ]


def test_total_row_equals_sum_of_day_buckets() -> None:
    """单桶 = 全区间：可加列 = 各日桶之和，比率在求和之后现算，行上是请求起止。"""
    day_rows = [
        _day(
            WED,
            visitors=10,
            pay_amount="1000",
            pay_orders=4,
            refund_amount="100",
            brushing_amount="50",
            ad_spend="60",
            promo_cost="40",
            live_pay_amount="30",
            live_ad_spend="3",
        ),
        _day(
            FRI,
            visitors=5,
            pay_amount="500.005",
            pay_orders=2,
            refund_amount="0.005",
            ad_spend="90",
            zhitongche_spend="20",
            promo_cost="10",
            refund_only_amount="25",
        ),
    ]
    date_from, date_to = WED - timedelta(days=40), FRI + timedelta(days=40)
    total = total_store_row(day_rows, date_from, date_to)
    days = bucket_store_rows(day_rows, "day", date_from, date_to)
    assert (total.date_from, total.date_to, total.day_count) == (date_from, date_to, 2)
    for key in ("visitors", "pay_orders", "refund_only_days"):
        assert getattr(total, key) == sum(getattr(d, key) for d in days), key
    for key in (
        "refund_amount",
        "brushing_amount",
        "ad_spend",
        "internal_spend",
        "promo_cost",
        "total_spend",
        "live_pay_amount",
        "live_ad_spend",
    ):
        assert getattr(total, key) == sum(getattr(d, key) for d in days), key
    # 周五手填直通车 20 → 当天站内 = 20（不是万相台 90）；周三无手填 → 60
    assert total.internal_spend == Decimal("80.00")
    assert total.zhitongche_spend == Decimal("20.00")
    assert total.ad_spend_total is None
    # 求和之后才取整：1000 + 500.005 = 1500.005 → 1500.01（逐天先取整会是 1500.00 + 0.01）
    assert total.pay_amount == Decimal("1500.01")
    assert total.actual_sales == Decimal("1350.00")  # 1500.005 − 100.005 − 50
    assert total.total_spend == Decimal("130.00")
    assert total.net_roi == Decimal("10.3846")  # 1350 / 130
    assert total.roi_ex_brushing == Decimal("11.1539")  # 1450.005 / 130
    assert total.refund_only_amount == Decimal("25.00")
    assert total.refund_only_rate == Decimal("0.0500")  # 25 / 500.005，只取已录那天


def test_total_row_on_empty_input() -> None:
    """没有日行也给一行（KPI 卡要有东西画）：金额 0、比率 None。"""
    total = total_store_row([], WED, FRI)
    assert (total.date_from, total.date_to, total.day_count) == (WED, FRI, 0)
    assert total.pay_amount == Decimal("0.00")
    assert total.net_roi is None and total.refund_only_amount is None
