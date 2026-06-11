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
