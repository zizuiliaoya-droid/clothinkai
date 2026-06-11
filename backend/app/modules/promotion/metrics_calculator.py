"""U04 衍生字段实时计算（EP05-S10/S11/S12）。

不持久化 — 每次响应实时计算。系数/阈值调整后所有历史 promotion 的展示按新值
（与 ``cost_snapshot`` 创建时快照不变形成对比，详见 BR-U04-31 历史不重算策略）。

3 个计算函数：
- ``calculate_effective_like_count``（EP05-S10）
- ``calculate_is_hit``（EP05-S11）
- ``calculate_cpl``（EP05-S12）
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.modules.promotion.legacy_settings import (
    HIT_THRESHOLD_LIKE_COUNT,
    PLATFORM_LIKE_COEFFICIENT,
)


def calculate_effective_like_count(
    *,
    platform: str,
    like_count: int | None,
) -> int | None:
    """BR-U04-31: 折算后点赞数。

    抖音 / 快手 系数 0.1（÷ 10）；小红书 / B 站 系数 1.0；未知平台默认 1.0。

    Args:
        platform: 平台名（必须命中 ``PLATFORM_LIKE_COEFFICIENT``，否则按 1.0 处理）。
        like_count: 原始点赞数；None 表示未采集，返回 None。

    Returns:
        折算后整数；ROUND_HALF_UP 取整。
    """
    if like_count is None:
        return None
    coefficient = PLATFORM_LIKE_COEFFICIENT.get(platform, Decimal("1.0"))
    return int(
        (Decimal(like_count) * coefficient).to_integral_value(
            rounding=ROUND_HALF_UP
        )
    )


def calculate_is_hit(
    *,
    like_count: int | None,
    threshold: int = HIT_THRESHOLD_LIKE_COUNT,
) -> bool:
    """BR-U04-32: 爆文判定。

    使用**原始** like_count 与阈值比较（与 effective_like_count 不同）。
    阈值调整后实时按新阈值判定。

    None / 0 → False。
    """
    if like_count is None:
        return False
    return like_count >= threshold


def calculate_cpl(
    *,
    quote_amount: Decimal,
    effective_like_count: int | None,
) -> Decimal | None:
    """BR-U04-33: 单赞成本（cost per like）。

    分母用 ``effective_like_count``（已折算），不是原始 like_count。
    分母为 None / 0 时返回 None（前端展示 "—"）。

    精度：DECIMAL(10, 4) ROUND_HALF_UP。
    """
    if effective_like_count is None or effective_like_count == 0:
        return None
    return (quote_amount / Decimal(effective_like_count)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


__all__ = [
    "calculate_cpl",
    "calculate_effective_like_count",
    "calculate_is_hit",
]
