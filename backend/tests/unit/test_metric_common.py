"""U08 services/metric/common.safe_div 单元测试。"""

from __future__ import annotations

from decimal import Decimal

from app.services.metric.common import safe_div


def test_normal_division():
    assert safe_div(10, 4) == Decimal("2.5")


def test_quantize():
    assert safe_div(1, 3, quantize=Decimal("0.0001")) == Decimal("0.3333")


def test_zero_denominator_returns_none():
    assert safe_div(10, 0) is None
    assert safe_div(10, Decimal("0")) is None


def test_none_operands_return_none():
    assert safe_div(None, 5) is None
    assert safe_div(5, None) is None


def test_decimal_inputs():
    assert safe_div(Decimal("100.00"), Decimal("50")) == Decimal("2")


def test_negative_denominator_returns_none():
    """PRD 全局约束 3：分母计算后 ≤ 0 一律置 NULL。

    典型来源：``1 - 退货率`` 在退货率 > 1 时为负（跨期退款可能出现），
    以及「约稿量 − 召回量 − 取消量」被扣成负数。给出负比率只会误导。
    """
    assert safe_div(10, -1) is None
    assert safe_div(10, Decimal("-0.5")) is None
    assert safe_div(-10, -2) is None


def test_negative_numerator_still_divides():
    """分子为负是合法的（例如负退款额），只有分母受 ≤ 0 约束。"""
    assert safe_div(-10, 4) == Decimal("-2.5")
