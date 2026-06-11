"""U14 单元测试：投产报表 5 公式 safe_div 边界 + exclude_brushing 占位。"""

from __future__ import annotations

from decimal import Decimal

from app.services.metric import style_roi


class TestReturnRate:
    def test_normal(self) -> None:
        assert style_roi.return_rate(Decimal("30"), Decimal("100")) == Decimal("0.3000")

    def test_zero_denominator(self) -> None:
        assert style_roi.return_rate(Decimal("30"), Decimal("0")) is None

    def test_none(self) -> None:
        assert style_roi.return_rate(None, Decimal("100")) is None


class TestAddToCartCost:
    def test_normal(self) -> None:
        assert style_roi.add_to_cart_cost(Decimal("1000"), 50) == Decimal("20.0000")

    def test_zero_count(self) -> None:
        assert style_roi.add_to_cart_cost(Decimal("1000"), 0) is None


class TestNetRoi:
    def test_normal(self) -> None:
        assert style_roi.net_roi(Decimal("800"), Decimal("200")) == Decimal("4.0000")

    def test_zero_spend(self) -> None:
        assert style_roi.net_roi(Decimal("800"), Decimal("0")) is None

    def test_exclude_brushing_placeholder_no_effect(self) -> None:
        # V1 占位：exclude_brushing 不影响结果
        a = style_roi.net_roi(Decimal("800"), Decimal("200"), exclude_brushing=False)
        b = style_roi.net_roi(Decimal("800"), Decimal("200"), exclude_brushing=True)
        assert a == b == Decimal("4.0000")


class TestUnitDealCost:
    def test_missing_conversion_rate_returns_none(self) -> None:
        # V1 基础口径缺加购转化率 → null
        cost = style_roi.add_to_cart_cost(Decimal("1000"), 50)
        assert style_roi.unit_deal_cost(cost, None, Decimal("0.3")) is None

    def test_full_chain(self) -> None:
        cost = Decimal("20")
        # 20 / 0.5 / (1-0.2) = 40 / 0.8 = 50
        result = style_roi.unit_deal_cost(cost, Decimal("0.5"), Decimal("0.2"))
        assert result == Decimal("50.0000")

    def test_return_rate_1_zero_denominator(self) -> None:
        # 退货率=1 → (1-1)=0 分母 → null
        assert style_roi.unit_deal_cost(Decimal("20"), Decimal("0.5"), Decimal("1")) is None
