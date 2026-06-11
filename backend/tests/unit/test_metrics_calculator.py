"""U04 metrics_calculator 单元测试。

覆盖 BR-U04-31/32/33：
- effective_like_count 各平台系数
- is_hit 阈值边界
- cpl 0 分母防御 + 4 位精度
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.modules.promotion.metrics_calculator import (
    calculate_cpl,
    calculate_effective_like_count,
    calculate_is_hit,
)


class TestEffectiveLikeCount:
    @pytest.mark.parametrize(
        "platform, raw, expected",
        [
            ("小红书", 1000, 1000),
            ("抖音", 1000, 100),    # × 0.1
            ("快手", 5000, 500),
            ("B站", 800, 800),
            ("未知平台", 200, 200),  # 默认 1.0
            ("小红书", None, None),
            ("抖音", 0, 0),
            ("抖音", 15, 2),         # 1.5 → ROUND_HALF_UP → 2
            ("抖音", 25, 3),         # 2.5 → ROUND_HALF_UP → 3
        ],
    )
    def test_calculation(
        self, platform: str, raw: int | None, expected: int | None
    ) -> None:
        assert (
            calculate_effective_like_count(platform=platform, like_count=raw)
            == expected
        )


class TestIsHit:
    def test_above_threshold(self) -> None:
        assert calculate_is_hit(like_count=1500) is True

    def test_at_threshold(self) -> None:
        """1000 默认阈值 == is_hit (>= 比较)."""
        assert calculate_is_hit(like_count=1000) is True

    def test_below_threshold(self) -> None:
        assert calculate_is_hit(like_count=999) is False

    def test_zero(self) -> None:
        assert calculate_is_hit(like_count=0) is False

    def test_none(self) -> None:
        assert calculate_is_hit(like_count=None) is False

    def test_custom_threshold(self) -> None:
        assert calculate_is_hit(like_count=500, threshold=400) is True
        assert calculate_is_hit(like_count=500, threshold=600) is False

    def test_uses_raw_not_effective(self) -> None:
        """is_hit 用原始 like_count，不用折算（与 effective_like_count 不同）."""
        # 抖音 1500 折算后 = 150，但 is_hit 依旧用 1500 ≥ 1000
        assert calculate_is_hit(like_count=1500) is True


class TestCpl:
    def test_basic(self) -> None:
        # 500 / 100 = 5.0000
        result = calculate_cpl(
            quote_amount=Decimal("500.00"), effective_like_count=100
        )
        assert result == Decimal("5.0000")

    def test_zero_likes_returns_none(self) -> None:
        assert (
            calculate_cpl(
                quote_amount=Decimal("500.00"), effective_like_count=0
            )
            is None
        )

    def test_none_likes_returns_none(self) -> None:
        assert (
            calculate_cpl(
                quote_amount=Decimal("500.00"), effective_like_count=None
            )
            is None
        )

    def test_precision_4_digits(self) -> None:
        # 100 / 7 = 14.2857142...
        result = calculate_cpl(
            quote_amount=Decimal("100.00"), effective_like_count=7
        )
        assert result == Decimal("14.2857")

    def test_round_half_up(self) -> None:
        # 1 / 8 = 0.125 → 4 位 → 0.1250
        result = calculate_cpl(
            quote_amount=Decimal("1.00"), effective_like_count=8
        )
        assert result == Decimal("0.1250")
