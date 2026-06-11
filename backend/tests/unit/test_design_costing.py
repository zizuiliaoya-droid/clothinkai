"""U10a 单元测试：自动核价求和。"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.design.domain import compute_total_cost


@pytest.mark.unit
class TestComputeTotalCost:
    def test_sum(self) -> None:
        total = compute_total_cost(
            Decimal("10.50"), Decimal("5.25"), Decimal("4.25")
        )
        assert total == Decimal("20.00")

    def test_zero(self) -> None:
        assert compute_total_cost(Decimal("0"), Decimal("0"), Decimal("0")) == Decimal("0")

    def test_precision_preserved(self) -> None:
        total = compute_total_cost(
            Decimal("1299.99"), Decimal("0.01"), Decimal("100.00")
        )
        assert total == Decimal("1400.00")
