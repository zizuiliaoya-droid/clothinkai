"""导入 typed 列的宽松解析（千牛 055、万相台 062 共用）。

规则：去千分位与首尾空白；空串、``-``、认不出的值一律 ``None``，不编造 0 ——
「导出里没有这列」和「这列是 0」要分得开。

迁移里的回填 SQL（055 / 062）按同一规则写成 ``CASE``；062 的状态测试逐值对拍
``to_optional_int`` 与回填结果，改这里要同步改 SQL。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def to_optional_decimal(raw: Any) -> Decimal | None:
    """宽松解析小数：去空白与千分位；空串、"-"、认不出的值一律 None。"""
    if raw is None:
        return None
    s = str(raw).replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def to_optional_int(raw: Any) -> int | None:
    """宽松解析整数：``5.0`` 记 5，``5.5`` 记 None（不四舍五入）。"""
    value = to_optional_decimal(raw)
    if value is None or value != value.to_integral_value():
        return None
    return int(value)


__all__ = ["to_optional_decimal", "to_optional_int"]
