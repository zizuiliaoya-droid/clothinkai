"""平台指标配置的值对象 ``PlatformMetricSettings``（报表视图设计 2.7 / 2.8）。

每个请求 / 每个任务按租户读一次（``PlatformMetricSettingService.get_settings``），再显式传给
所有用到折算点赞或爆文阈值的地方。读取函数的 ``settings`` 参数不给默认值：以前系数和阈值
是模块常量，SQL 在导入时就拼死了，后台改了也不生效。

缺行回落（不报错）：
- 5 个平台（``Platform`` 枚举）缺行 → 用该平台默认值（抖音阈值仍是 10000）
- 5 个平台之外（例如录错的平台名）→ 系数 ``FALLBACK_LIKE_COEFFICIENT``、阈值 ``FALLBACK_HIT_THRESHOLD``

SQL 片段不在导入时拼系数：系数 / 阈值走 ``sql_params()`` 给的两个 jsonb 绑定参数
（``:like_coef``、``:hit_th``），改了下一次查询就生效。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.modules.blogger.enums import Platform

DEFAULT_LIKE_COEFFICIENTS: Mapping[str, Decimal] = {
    Platform.XIAOHONGSHU.value: Decimal("1.0"),
    Platform.DOUYIN.value: Decimal("0.1"),  # 抖音点赞 ÷ 10
    Platform.KUAISHOU.value: Decimal("0.1"),
    Platform.BILIBILI.value: Decimal("1.0"),
    Platform.DEWU.value: Decimal("1.0"),
}
"""点赞折算系数默认值（= 原来写死的那份常量，上线时数字不变）。"""

DEFAULT_HIT_THRESHOLDS: Mapping[str, int] = {
    Platform.XIAOHONGSHU.value: 1000,
    Platform.DOUYIN.value: 10000,
    Platform.KUAISHOU.value: 1000,
    Platform.BILIBILI.value: 1000,
    Platform.DEWU.value: 1000,
}
"""爆文阈值默认值（原始点赞，不折算）。"""

FALLBACK_LIKE_COEFFICIENT = Decimal("1")
"""5 个平台之外的折算系数。"""

FALLBACK_HIT_THRESHOLD = 1000
"""5 个平台之外的爆文阈值。"""


def _coef_text(value: Decimal) -> str:
    """系数 → 定点小数文本，去掉尾零（``0.100`` → ``0.1``、``10.000`` → ``10``）。

    不经 float：SQL 里乘出来的 numeric 标度与旧字面量（``* 0.1``）一致，页面数字不变。
    """
    return format(value.normalize(), "f")


_FALLBACK_COEF_SQL = _coef_text(FALLBACK_LIKE_COEFFICIENT)


def effective_like_sum(col: str, platform_col: str) -> str:
    """折算后点赞求和 SQL 片段（设计 2.8）。查询要带 ``sql_params()`` 的 ``like_coef``。

    ``col`` / ``platform_col`` 是调用方写死的列名，不是用户输入。
    """
    return (
        f"COALESCE(SUM({col} * COALESCE((CAST(:like_coef AS jsonb) ->> {platform_col})::numeric, "
        f"{_FALLBACK_COEF_SQL})), 0)"
    )


def hit_condition_sql(like_col: str, platform_col: str) -> str:
    """爆文判定 SQL 条件（设计 2.7，原始点赞）。查询要带 ``sql_params()`` 的 ``hit_th``。

    点赞为空不算爆文；``NOT (…)`` 时点赞为空也落在「非爆文」一侧。
    """
    return (
        f"({like_col} IS NOT NULL AND {like_col} >= "
        f"COALESCE((CAST(:hit_th AS jsonb) ->> {platform_col})::int, {FALLBACK_HIT_THRESHOLD}))"
    )


@dataclass(frozen=True)
class PlatformMetricSettings:
    """一个租户的平台指标：两张表都总含 5 个平台（缺行已按默认补上）。"""

    like_coefficients: Mapping[str, Decimal]
    hit_thresholds: Mapping[str, int]

    def like_coefficient(self, platform: str) -> Decimal:
        return self.like_coefficients.get(platform, FALLBACK_LIKE_COEFFICIENT)

    def hit_threshold(self, platform: str) -> int:
        return self.hit_thresholds.get(platform, FALLBACK_HIT_THRESHOLD)

    def sql_params(self) -> dict[str, str]:
        """``effective_like_sum`` / ``hit_condition_sql`` 需要的两个 jsonb 绑定参数。"""
        return {
            "like_coef": json.dumps(
                {p: _coef_text(c) for p, c in self.like_coefficients.items()},
                ensure_ascii=False,
            ),
            "hit_th": json.dumps(
                {p: int(t) for p, t in self.hit_thresholds.items()}, ensure_ascii=False
            ),
        }

    def rows(self) -> list[tuple[str, Decimal, int]]:
        """5 个平台按枚举顺序：``(平台, 系数, 阈值)``。"""
        return [
            (p.value, self.like_coefficient(p.value), self.hit_threshold(p.value)) for p in Platform
        ]

    @classmethod
    def defaults(cls) -> PlatformMetricSettings:
        return cls(
            like_coefficients=dict(DEFAULT_LIKE_COEFFICIENTS),
            hit_thresholds=dict(DEFAULT_HIT_THRESHOLDS),
        )

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[str, Decimal, int]]) -> PlatformMetricSettings:
        """表里的行 ``(平台, 系数, 阈值)`` 盖在默认值上：缺哪个平台就用哪个平台的默认值。"""
        coefs = dict(DEFAULT_LIKE_COEFFICIENTS)
        thresholds = dict(DEFAULT_HIT_THRESHOLDS)
        for platform, coef, threshold in rows:
            coefs[platform] = Decimal(coef)
            thresholds[platform] = int(threshold)
        return cls(like_coefficients=coefs, hit_thresholds=thresholds)


__all__ = [
    "DEFAULT_HIT_THRESHOLDS",
    "DEFAULT_LIKE_COEFFICIENTS",
    "FALLBACK_HIT_THRESHOLD",
    "FALLBACK_LIKE_COEFFICIENT",
    "PlatformMetricSettings",
    "effective_like_sum",
    "hit_condition_sql",
]
