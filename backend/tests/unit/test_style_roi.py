"""U14 单元测试：投产报表 5 公式 safe_div 边界与刷单口径标识。"""

from __future__ import annotations

from decimal import Decimal

import pytest

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

    def test_exclude_brushing_is_upstream_contract(self) -> None:
        # 刷单剔除由仓储聚合完成，公式函数仅接收已调整的 confirmed_amount。
        a = style_roi.net_roi(Decimal("800"), Decimal("200"), exclude_brushing=False)
        b = style_roi.net_roi(Decimal("800"), Decimal("200"), exclude_brushing=True)
        assert a == b == Decimal("4.0000")


class TestPreReturnRoi:
    def test_normal(self) -> None:
        # 支付 1000 / 总推广成本 400 = 2.5（不扣退款，区别于净投产比）
        assert style_roi.pre_return_roi(Decimal("1000"), Decimal("400")) == Decimal("2.5000")

    @pytest.mark.parametrize("spend", [Decimal("0"), Decimal("-1"), None])
    def test_bad_denominator(self, spend: Decimal | None) -> None:
        assert style_roi.pre_return_roi(Decimal("1000"), spend) is None


class TestPromoCostPerUnit:
    def test_normal(self) -> None:
        # 总推广成本 100 / 支付件数 8 = 12.5
        assert style_roi.promo_cost_per_unit(Decimal("100"), 8) == Decimal("12.5000")

    @pytest.mark.parametrize("orders", [0, -3, None])
    def test_bad_denominator(self, orders: int | None) -> None:
        assert style_roi.promo_cost_per_unit(Decimal("100"), orders) is None

    def test_old_formula_removed(self) -> None:
        # 旧公式「加购成本 / 加购转化率 / (1 - 退货率)」恒为空，R3 删掉
        assert not hasattr(style_roi, "unit_deal_cost")


class TestAdAddCartCost:
    def test_normal(self) -> None:
        # 站内投放 90 / 站内加购 9 = 10（复用 add_to_cart_cost）
        assert style_roi.add_to_cart_cost(Decimal("90"), 9) == Decimal("10.0000")

    def test_no_cart(self) -> None:
        assert style_roi.add_to_cart_cost(Decimal("90"), 0) is None


class TestOffsiteAddCart:
    def test_no_paired_day_is_none(self) -> None:
        # 区间里千牛与万相台没有同一天的数据 → null（页面「—」），不是 0
        assert style_roi.offsite_add_cart(0, 0, 0) is None
        assert style_roi.offsite_add_cart(30, 10, 0) is None

    def test_sum_first_then_floor(self) -> None:
        # 两天各 +5、−8：先求和 −3 再取 0 → 0；逐日取 0 再求和会得 5
        assert style_roi.offsite_add_cart(15 + 2, 10 + 10, 2) == 0

    def test_normal_difference(self) -> None:
        assert style_roi.offsite_add_cart(20, 9, 1) == 11


class TestGrossMargin:
    """毛利三列（默 12）：总成本 = 支付件数 × 单品成本；毛利 = 实际销售 − 总成本；利润率 = 毛利 / 实际销售。"""

    def test_normal(self) -> None:
        assert style_roi.gross_margin(Decimal("900.00"), 6, Decimal("50.50")) == (
            Decimal("303.00"),
            Decimal("597.00"),
            Decimal("0.6633"),
        )

    def test_missing_cost_is_all_none(self) -> None:
        # 任一启用成员没有成本 → 三列都 null，不当 0
        assert style_roi.gross_margin(Decimal("900.00"), 6, None) == (None, None, None)

    def test_zero_cost_is_not_missing(self) -> None:
        # 成本录了 0 是真 0（不是缺）
        assert style_roi.gross_margin(Decimal("100.00"), 2, Decimal("0")) == (
            Decimal("0.00"),
            Decimal("100.00"),
            Decimal("1.0000"),
        )

    @pytest.mark.parametrize("confirmed", [Decimal("0.00"), Decimal("-50.00")])
    def test_rate_none_when_sales_not_positive(self, confirmed: Decimal) -> None:
        # 实际销售 ≤ 0（只有刷单的普通行等）→ 利润率 null，总成本与毛利照算
        cost, profit, rate = style_roi.gross_margin(confirmed, 3, Decimal("10.00"))
        assert (cost, profit, rate) == (Decimal("30.00"), confirmed - Decimal("30.00"), None)

    def test_negative_margin(self) -> None:
        assert style_roi.gross_margin(Decimal("100.00"), 4, Decimal("40.00")) == (
            Decimal("160.00"),
            Decimal("-60.00"),
            Decimal("-0.6000"),
        )
