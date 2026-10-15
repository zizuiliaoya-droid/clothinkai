"""``PlatformMetricSettings`` 值对象与 SQL 片段（7e R7，报表视图设计 2.7 / 2.8）。

- 两种回落：5 个平台缺行用该平台默认（抖音阈值仍 10000）；5 个平台之外系数 1、阈值 1000
- ``sql_params()`` 逐字：系数走定点小数文本、去尾零（``0.100`` → ``"0.1"``，不经 float）
- SQL 片段逐字：系数 / 阈值是绑定参数，不在导入时拼进 SQL
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.modules.metric_config.settings import (
    DEFAULT_HIT_THRESHOLDS,
    DEFAULT_LIKE_COEFFICIENTS,
    FALLBACK_HIT_THRESHOLD,
    FALLBACK_LIKE_COEFFICIENT,
    PlatformMetricSettings,
    effective_like_sum,
    hit_condition_sql,
)
from app.modules.promotion.metrics_calculator import calculate_effective_like_count

_ODD = "YT7612472372899"


def test_defaults_match_design() -> None:
    assert dict(DEFAULT_LIKE_COEFFICIENTS) == {
        "小红书": Decimal("1.0"),
        "抖音": Decimal("0.1"),
        "快手": Decimal("0.1"),
        "B站": Decimal("1.0"),
        "得物": Decimal("1.0"),
    }
    assert dict(DEFAULT_HIT_THRESHOLDS) == {
        "小红书": 1000,
        "抖音": 10000,
        "快手": 1000,
        "B站": 1000,
        "得物": 1000,
    }
    assert FALLBACK_LIKE_COEFFICIENT == Decimal("1")
    assert FALLBACK_HIT_THRESHOLD == 1000


def test_missing_rows_fall_back_to_platform_defaults() -> None:
    """只配了小红书：其余 4 个平台用各自默认值，不是统一的 1 / 1000。"""
    s = PlatformMetricSettings.from_rows([("小红书", Decimal("2.000"), 2000)])
    assert s.like_coefficient("小红书") == Decimal("2.000")
    assert s.hit_threshold("小红书") == 2000
    assert s.like_coefficient("抖音") == Decimal("0.1")
    assert s.hit_threshold("抖音") == 10000
    assert s.like_coefficient("快手") == Decimal("0.1")
    assert s.hit_threshold("得物") == 1000
    assert set(s.like_coefficients) == set(s.hit_thresholds) == set(DEFAULT_LIKE_COEFFICIENTS)


def test_no_rows_equals_defaults() -> None:
    assert PlatformMetricSettings.from_rows([]) == PlatformMetricSettings.defaults()


def test_platform_outside_enum_falls_back() -> None:
    s = PlatformMetricSettings.from_rows([("抖音", Decimal("0.500"), 500)])
    assert s.like_coefficient(_ODD) == Decimal("1")
    assert s.hit_threshold(_ODD) == 1000


def test_sql_params_defaults_verbatim() -> None:
    assert PlatformMetricSettings.defaults().sql_params() == {
        "like_coef": '{"小红书": "1", "抖音": "0.1", "快手": "0.1", "B站": "1", "得物": "1"}',
        "hit_th": '{"小红书": 1000, "抖音": 10000, "快手": 1000, "B站": 1000, "得物": 1000}',
    }


def test_sql_params_normalizes_db_scale() -> None:
    """库里是 numeric(6,3)：``0.100`` / ``10.000`` / ``2.500`` 要出 ``0.1`` / ``10`` / ``2.5``。

    与旧字面量 ``* 0.1`` 同标度，页面数字不变；也不能是 ``1E+1`` 这种科学计数法。
    """
    s = PlatformMetricSettings.from_rows(
        [
            ("抖音", Decimal("0.100"), 10000),
            ("小红书", Decimal("10.000"), 1),
            ("快手", Decimal("2.500"), 10_000_000),
            ("B站", Decimal("0.001"), 1000),
        ]
    )
    coef = json.loads(s.sql_params()["like_coef"])
    assert coef == {"小红书": "10", "抖音": "0.1", "快手": "2.5", "B站": "0.001", "得物": "1"}
    assert json.loads(s.sql_params()["hit_th"])["快手"] == 10_000_000


def test_rows_in_enum_order() -> None:
    assert PlatformMetricSettings.defaults().rows() == [
        ("小红书", Decimal("1.0"), 1000),
        ("抖音", Decimal("0.1"), 10000),
        ("快手", Decimal("0.1"), 1000),
        ("B站", Decimal("1.0"), 1000),
        ("得物", Decimal("1.0"), 1000),
    ]


@pytest.mark.parametrize(
    "col, platform_col, expected",
    [
        (
            "like_count",
            "platform",
            "COALESCE(SUM(like_count * COALESCE((CAST(:like_coef AS jsonb) ->> platform)::numeric,"
            " 1)), 0)",
        ),
        (
            "p.like_count",
            "p.platform",
            "COALESCE(SUM(p.like_count * COALESCE((CAST(:like_coef AS jsonb) ->> p.platform)"
            "::numeric, 1)), 0)",
        ),
    ],
)
def test_effective_like_sum_text(col: str, platform_col: str, expected: str) -> None:
    sql = effective_like_sum(col, platform_col)
    assert sql == expected
    # 不在导入时拼系数：片段里没有任何平台名或系数字面量
    assert "抖音" not in sql and "0.1" not in sql


def test_hit_condition_sql_text() -> None:
    assert hit_condition_sql("p.like_count", "p.platform") == (
        "(p.like_count IS NOT NULL AND p.like_count >= "
        "COALESCE((CAST(:hit_th AS jsonb) ->> p.platform)::int, 1000))"
    )


@pytest.mark.parametrize(
    "platform, raw, expected",
    [
        ("抖音", 1500, 750),  # 0.5
        ("小红书", 101, 202),  # 2.0
        ("快手", 1500, 150),  # 没配，默认 0.1
        (_ODD, 7, 7),  # 5 个平台之外按 1
        ("抖音", 3, 2),  # 1.5 → ROUND_HALF_UP → 2
        ("抖音", None, None),
    ],
)
def test_effective_like_count_reads_settings(
    platform: str, raw: int | None, expected: int | None
) -> None:
    s = PlatformMetricSettings.from_rows(
        [("抖音", Decimal("0.500"), 10000), ("小红书", Decimal("2.000"), 1000)]
    )
    assert calculate_effective_like_count(platform=platform, like_count=raw, settings=s) == expected
