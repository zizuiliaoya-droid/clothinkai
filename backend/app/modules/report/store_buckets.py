"""店铺日 / 周 / 月：日行逐天套默 13 与仅退款，再按桶求和（7e R4，设计 2.4）。

输入是 ``StoreDailyRepository.aggregate`` / ``SummaryReadRepository.store_daily`` 的日行
（两条路径同一套键，手填层已 LEFT JOIN）。规则只在这里一份，周 / 月**不读**周 / 月汇总表：

1. 逐天：站内推广费 = 当天手填了全站推 / 直通车 / 引力魔方任一项（手填 0 也算填了）就用
   三项之和（没填的记 0），否则万相台合计（默 13）；仅退款非 NULL 就算「已录」。
2. 按「自然日 / ISO 周 / 自然月 ∩ 请求区间」归桶求和，行上标实际起止。
   可加列保留精确值相加，求和之后才取两位小数（逐天先取整会差分）。
3. 比率在求和之后用 ``safe_div`` 现算；仅退款率的分子分母都只取已录的日子（默 14）。
   「其中直播」两列只展示，不参与公式。
"""

from __future__ import annotations

import calendar
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.modules.report.advanced_schemas import StoreReportRow
from app.modules.report.domain import bucket_start
from app.services.metric.common import safe_div

_CENT = Decimal("0.01")
_Q4 = Decimal("0.0001")
_ZERO = Decimal("0")

_MANUAL = ("ad_spend_total", "zhitongche_spend", "yinli_spend")
_SUMMED = (
    "pay_amount",
    "refund_amount",
    "brushing_amount",
    "ad_spend",
    "promo_cost",
    "live_pay_amount",
    "live_ad_spend",
)


def _dec(value: Any) -> Decimal:
    return _ZERO if value is None else Decimal(str(value))


def _cent(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _bucket_end(start: date, granularity: str) -> date:
    if granularity == "day":
        return start
    if granularity == "week":
        return start + timedelta(days=6)
    if granularity == "month":
        return start.replace(day=calendar.monthrange(start.year, start.month)[1])
    if granularity == "year":
        return start.replace(month=12, day=31)
    raise ValueError(f"不支持的时间粒度: {granularity}")


@dataclass
class _Bucket:
    visitors: int = 0
    pay_orders: int = 0
    sums: dict[str, Decimal] = field(default_factory=lambda: {k: _ZERO for k in _SUMMED})
    manual: dict[str, Decimal | None] = field(default_factory=lambda: dict.fromkeys(_MANUAL))
    internal_spend: Decimal = _ZERO
    refund_only_amount: Decimal | None = None
    refund_only_pay: Decimal = _ZERO
    refund_only_days: int = 0
    day_count: int = 0

    def add_day(self, row: Mapping[str, Any]) -> None:
        self.day_count += 1
        self.visitors += int(row["visitors"] or 0)
        self.pay_orders += int(row["pay_orders"] or 0)
        for key in _SUMMED:
            self.sums[key] += _dec(row[key])
        # 默 13：按天判断，手填三项任一非 NULL 就整天用手填三项之和
        manual_values = [row[k] for k in _MANUAL]
        if any(v is not None for v in manual_values):
            self.internal_spend += sum((_dec(v) for v in manual_values), _ZERO)
        else:
            self.internal_spend += _dec(row["ad_spend"])
        for key, value in zip(_MANUAL, manual_values, strict=True):
            if value is not None:
                current = self.manual[key]
                self.manual[key] = _dec(value) if current is None else current + _dec(value)
        # 默 14：只有录了仅退款的日子进仅退款率的分子分母
        refund_only = row["refund_only_amount"]
        if refund_only is not None:
            self.refund_only_days += 1
            self.refund_only_pay += _dec(row["pay_amount"])
            prev = self.refund_only_amount
            self.refund_only_amount = _dec(refund_only) + (prev if prev is not None else _ZERO)

    def to_row(self, date_from: date, date_to: date) -> StoreReportRow:
        pay = self.sums["pay_amount"]
        brushing = self.sums["brushing_amount"]
        actual_sales = pay - self.sums["refund_amount"] - brushing
        promo_cost = self.sums["promo_cost"]
        total_spend = self.internal_spend + promo_cost
        manual = {k: (None if v is None else _cent(v)) for k, v in self.manual.items()}
        return StoreReportRow(
            date_from=date_from,
            date_to=date_to,
            visitors=self.visitors,
            pay_amount=_cent(pay),
            pay_orders=self.pay_orders,
            refund_amount=_cent(self.sums["refund_amount"]),
            brushing_amount=_cent(brushing),
            actual_sales=_cent(actual_sales),
            ad_spend=_cent(self.sums["ad_spend"]),
            ad_spend_total=manual["ad_spend_total"],
            zhitongche_spend=manual["zhitongche_spend"],
            yinli_spend=manual["yinli_spend"],
            internal_spend=_cent(self.internal_spend),
            promo_cost=_cent(promo_cost),
            total_spend=_cent(total_spend),
            live_pay_amount=_cent(self.sums["live_pay_amount"]),
            live_ad_spend=_cent(self.sums["live_ad_spend"]),
            refund_only_amount=(
                None if self.refund_only_amount is None else _cent(self.refund_only_amount)
            ),
            total_spend_ratio=safe_div(total_spend, pay, quantize=_Q4),
            internal_ratio=safe_div(self.internal_spend, actual_sales, quantize=_Q4),
            external_ratio=safe_div(promo_cost, actual_sales, quantize=_Q4),
            net_spend_ratio=safe_div(total_spend, actual_sales, quantize=_Q4),
            net_roi=safe_div(actual_sales, total_spend, quantize=_Q4),
            roi_ex_brushing=safe_div(pay - brushing, total_spend, quantize=_Q4),
            refund_only_rate=safe_div(self.refund_only_amount, self.refund_only_pay, quantize=_Q4),
            refund_only_days=self.refund_only_days,
            day_count=self.day_count,
        )


def bucket_store_rows(
    day_rows: Iterable[Mapping[str, Any]],
    granularity: str,
    date_from: date,
    date_to: date,
) -> list[StoreReportRow]:
    """日行 → 日 / 周 / 月 / 年行（按桶升序）。``granularity`` 只认 day / week / month / year
    （year 只给 BI 趋势用；店铺三页的接口只收 day / week / month）。

    行上 ``date_from`` / ``date_to`` = 桶 ∩ ``[date_from, date_to]``；``extra`` 留空，由调用方填。
    """
    _bucket_end(date_from, granularity)  # 先校验粒度，空输入也报错
    buckets: dict[date, _Bucket] = {}
    for row in day_rows:
        start = bucket_start(row["date"], granularity)
        buckets.setdefault(start, _Bucket()).add_day(row)
    return [
        buckets[start].to_row(max(start, date_from), min(_bucket_end(start, granularity), date_to))
        for start in sorted(buckets)
    ]


def total_store_row(
    day_rows: Iterable[Mapping[str, Any]], date_from: date, date_to: date
) -> StoreReportRow:
    """整个区间合成一行（BI 店铺 KPI 卡，7e R6）：同一个 ``_Bucket``，公式与分桶一致。
    没有日行也给一行（金额 0、比率 None）；行上是请求起止。
    """
    bucket = _Bucket()
    for row in day_rows:
        bucket.add_day(row)
    return bucket.to_row(date_from, date_to)


__all__ = ["bucket_store_rows", "total_store_row"]
