"""U14 投产报表指标（style ROI）。

5 核心公式均经 safe_div（分母 0/None→null）。
exclude_brushing 形参占位（V1 默认 False 不影响；U16 启用剔除 order_adjustment）。
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


def unit_deal_cost(
    add_cart_cost_value: Decimal | None,
    add_cart_conversion_rate: Decimal | None,
    return_rate_value: Decimal | None,
) -> Decimal | None:
    """推广单件成交成本 = 加购成本 / 加购转化率 / (1 - 退货率)。

    链式 safe_div；任一分母缺失/0 → null（V1 基础口径缺加购转化率字段 → 多为 null）。
    """
    step1 = safe_div(add_cart_cost_value, add_cart_conversion_rate)
    if step1 is None:
        return None
    denom = None if return_rate_value is None else (Decimal("1") - return_rate_value)
    return safe_div(step1, denom, quantize=_Q4)


__all__ = [
    "add_to_cart_cost",
    "net_roi",
    "return_rate",
    "unit_deal_cost",
]
