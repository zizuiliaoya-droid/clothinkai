"""U14 投产报表指标（style ROI）。

比率一律经 safe_div（分母 ≤ 0 / None → null），可加列先求和、再在这里算比率（7e R3，设计 2.3）。
exclude_brushing 是口径标识；刷单金额由上游聚合在计算 confirmed_amount 前剔除。
"""

from __future__ import annotations

from decimal import Decimal

from app.services.metric.common import safe_div

_Q4 = Decimal("0.0001")


def return_rate(
    refund_amount: Decimal | int | None, pay_amount: Decimal | int | None
) -> Decimal | None:
    """退货退款率 = 成功退款金额 / 支付金额。"""
    return safe_div(refund_amount, pay_amount, quantize=_Q4)


def add_to_cart_cost(
    total_spend: Decimal | int | None, add_cart_count: int | None
) -> Decimal | None:
    """加购成本 = 推广总花费 / 总加购数。"""
    return safe_div(total_spend, add_cart_count, quantize=_Q4)


def net_roi(
    confirmed_amount: Decimal | int | None,
    total_spend: Decimal | int | None,
    *,
    exclude_brushing: bool = False,
) -> Decimal | None:
    """净投产比 = 待确认收货金额 / 推广总花费。

    exclude_brushing（U16 启用）：为 true 时上游 confirmed_amount 已基于剔除刷单后的
    pay_amount 计算（ProductionRepository.aggregate_by_style 已减去刷单金额），
    本函数公式不变，仅作口径标识。
    """
    _ = exclude_brushing  # 口径标识：剔除在 aggregate_by_style 层完成
    return safe_div(confirmed_amount, total_spend, quantize=_Q4)


def pre_return_roi(
    pay_amount: Decimal | int | None, total_spend: Decimal | int | None
) -> Decimal | None:
    """退货前投产比 = 支付金额 / 总推广成本。"""
    return safe_div(pay_amount, total_spend, quantize=_Q4)


def promo_cost_per_unit(
    total_spend: Decimal | int | None, pay_orders: int | None
) -> Decimal | None:
    """推广单件成交成本 = 总推广成本 / 支付件数（默 11；支付件数不受刷单开关影响）。"""
    return safe_div(total_spend, pay_orders, quantize=_Q4)


def offsite_add_cart(paired_all_cart: int, paired_ad_cart: int, paired_days: int) -> int | None:
    """站外加购 = max(Σ配对日总加购 − Σ配对日站内加购, 0)。

    入参是区间**求和之后**的值（两天 +5 / −8 合计 −3 → 0，不是逐日取 0 的 5）。
    没有一天是千牛与万相台都有数据的（``paired_days = 0``）→ null，页面显示「—」。
    """
    if paired_days <= 0:
        return None
    return max(paired_all_cart - paired_ad_cart, 0)


__all__ = [
    "add_to_cart_cost",
    "net_roi",
    "offsite_add_cart",
    "pre_return_roi",
    "promo_cost_per_unit",
    "return_rate",
]
