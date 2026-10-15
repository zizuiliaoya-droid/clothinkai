"""U04 metrics_calculator 单元测试。

覆盖 BR-U04-31/32/33：
- effective_like_count 各平台系数
- is_hit 阈值边界
- cpl 0 分母防御 + 4 位精度
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.modules.metric_config.settings import PlatformMetricSettings
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
            ("抖音", 1000, 100),  # × 0.1
            ("快手", 5000, 500),
            ("B站", 800, 800),
            ("得物", 800, 800),
            ("未知平台", 200, 200),  # 默认 1.0
            ("小红书", None, None),
            ("抖音", 0, 0),
            ("抖音", 15, 2),  # 1.5 → ROUND_HALF_UP → 2
            ("抖音", 25, 3),  # 2.5 → ROUND_HALF_UP → 3
        ],
    )
    def test_calculation(self, platform: str, raw: int | None, expected: int | None) -> None:
        assert (
            calculate_effective_like_count(
                platform=platform, like_count=raw, settings=PlatformMetricSettings.defaults()
            )
            == expected
        )


_DEFAULTS = PlatformMetricSettings.defaults()


def _hit(
    platform: str, like_count: int | None, settings: PlatformMetricSettings = _DEFAULTS
) -> bool:
    return calculate_is_hit(platform=platform, like_count=like_count, settings=settings)


class TestIsHit:
    def test_above_threshold(self) -> None:
        assert _hit("小红书", 1500) is True

    def test_at_threshold(self) -> None:
        """小红书默认阈值 1000 == is_hit (>= 比较)."""
        assert _hit("小红书", 1000) is True

    def test_below_threshold(self) -> None:
        assert _hit("小红书", 999) is False

    def test_zero(self) -> None:
        assert _hit("小红书", 0) is False

    def test_none(self) -> None:
        assert _hit("小红书", None) is False

    def test_platform_threshold(self) -> None:
        """抖音默认阈值 10000；配置后按配置值判。"""
        assert _hit("抖音", 9999) is False
        assert _hit("抖音", 10000) is True
        configured = PlatformMetricSettings.from_rows([("抖音", Decimal("0.1"), 400)])
        assert _hit("抖音", 500, configured) is True
        assert _hit("小红书", 500, configured) is False  # 没配的平台仍是默认 1000

    def test_unknown_platform_fallback(self) -> None:
        """5 个平台之外按 1000 判。"""
        assert _hit("未知平台", 1000) is True
        assert _hit("未知平台", 999) is False

    def test_uses_raw_not_effective(self) -> None:
        """is_hit 用原始 like_count，不用折算（与 effective_like_count 不同）."""
        # 快手 1500 折算后 = 150，但 is_hit 依旧用 1500 ≥ 1000
        assert _hit("快手", 1500) is True


_RECORDED = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)


class TestCpl:
    """单篇点赞成本 = 总推广成本 ÷ 7 天点赞数，录过 7 天数据才算（PRD V1.4 §9）。"""

    def test_basic(self) -> None:
        # 总推广成本 500 / 100 = 5.0000
        result = calculate_cpl(
            total_promo_cost=Decimal("500.00"),
            effective_like_count=100,
            metrics_recorded_at=_RECORDED,
        )
        assert result == Decimal("5.0000")

    def test_not_recorded_returns_none(self) -> None:
        """有点赞数但没录过 7 天数据（编辑或采集写进来的）也不算。"""
        assert (
            calculate_cpl(
                total_promo_cost=Decimal("500.00"),
                effective_like_count=100,
                metrics_recorded_at=None,
            )
            is None
        )

    def test_missing_cost_returns_none(self) -> None:
        assert (
            calculate_cpl(
                total_promo_cost=None, effective_like_count=100, metrics_recorded_at=_RECORDED
            )
            is None
        )

    def test_zero_likes_returns_none(self) -> None:
        assert (
            calculate_cpl(
                total_promo_cost=Decimal("500.00"),
                effective_like_count=0,
                metrics_recorded_at=_RECORDED,
            )
            is None
        )

    def test_none_likes_returns_none(self) -> None:
        assert (
            calculate_cpl(
                total_promo_cost=Decimal("500.00"),
                effective_like_count=None,
                metrics_recorded_at=_RECORDED,
            )
            is None
        )

    def test_precision_4_digits(self) -> None:
        # 100 / 7 = 14.2857142...
        result = calculate_cpl(
            total_promo_cost=Decimal("100.00"),
            effective_like_count=7,
            metrics_recorded_at=_RECORDED,
        )
        assert result == Decimal("14.2857")

    def test_round_half_up(self) -> None:
        # 1 / 8 = 0.125 → 4 位 → 0.1250
        result = calculate_cpl(
            total_promo_cost=Decimal("1.00"), effective_like_count=8, metrics_recorded_at=_RECORDED
        )
        assert result == Decimal("0.1250")
