"""指标计算公共工具（U08）。

``safe_div``：分母 ≤ 0 / None → None（前端展示 "—"，与 U04 metrics_calculator 一致语义）。
V1 报表（work_progress / style_roi）复用。
"""

from __future__ import annotations

from decimal import Decimal


def safe_div(
    numerator: Decimal | int | float | None,
    denominator: Decimal | int | float | None,
    *,
    quantize: Decimal | None = None,
) -> Decimal | None:
    """安全除法：分母 ≤ 0 / None 或分子 None → None。

    分母为负一律置空，不是为了躲除零，而是因为业务上没有意义：
    比率的分母是「总量」（花费、约稿量、支付金额），算出负数说明口径已经失真
    （例如退货率 > 1 时 ``1 - 退货率`` 为负，或召回量+取消量超过约稿量），
    此时给出一个负比率只会误导，不如留空。

    Args:
        quantize: 若提供，对结果做 ``quantize``（如 ``Decimal("0.0001")``）。
    """
    if numerator is None or denominator is None:
        return None
    den = Decimal(str(denominator))
    if den <= 0:
        return None
    result = Decimal(str(numerator)) / den
    return result.quantize(quantize) if quantize is not None else result


__all__ = ["safe_div"]
