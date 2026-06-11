"""指标计算公共工具（U08）。

``safe_div``：分母为 0 / None → None（前端展示 "—"，与 U04 metrics_calculator 一致语义）。
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
    """安全除法：分母 0 / None 或分子 None → None。

    Args:
        quantize: 若提供，对结果做 ``quantize``（如 ``Decimal("0.0001")``）。
    """
    if numerator is None or denominator is None:
        return None
    den = Decimal(str(denominator))
    if den == 0:
        return None
    result = Decimal(str(numerator)) / den
    return result.quantize(quantize) if quantize is not None else result


__all__ = ["safe_div"]
