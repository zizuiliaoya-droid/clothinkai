"""U04 衍生字段实时计算（EP05-S10/S11/S12）。

不持久化 — 每次响应实时计算。系数/阈值调整后所有历史 promotion 的展示按新值
（与 ``cost_snapshot`` 创建时快照不变形成对比，详见 BR-U04-31 历史不重算策略）。

3 个计算函数：
- ``calculate_effective_like_count``（EP05-S10）
- ``calculate_is_hit``（EP05-S11）
- ``calculate_cpl``（EP05-S12）
"""

from __future__ import annotations

from datetime import datetime
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
    return int((Decimal(like_count) * coefficient).to_integral_value(rounding=ROUND_HALF_UP))


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
    total_promo_cost: Decimal | None,
    effective_like_count: int | None,
    metrics_recorded_at: datetime | None,
) -> Decimal | None:
    """单篇点赞成本（PRD V1.4 §9）= 总推广成本 ÷ 7 天点赞数。

    - 分子是站外推广成本 ``total_promo_cost``（博主服务费 + 样品成本 + 寄回运费；寄拍样品
      成本恒 0、置换服务费恒 0 由生成列与 ``_enforce_mode_costs`` 保证）。以前只用博主服务费，
      置换单的单赞成本永远是 0，寄拍单漏了寄回运费。
    - **录过 7 天数据才算**（``metrics_recorded_at`` 有值）：PRD「仅 PR 提交 7 天点赞数据后
      才计算」，业务方 10-02 再次确认。``like_count`` 还能被编辑、被采集器写进来，不能当门槛。
    - 分母用折算后点赞（抖音 / 快手 ×0.1）；None / 0 → None（前端展示「—」）。

    精度：DECIMAL(10, 4) ROUND_HALF_UP。
    """
    if metrics_recorded_at is None or total_promo_cost is None:
        return None
    if effective_like_count is None or effective_like_count == 0:
        return None
    return (total_promo_cost / Decimal(effective_like_count)).quantize(
        Decimal("0.0001"), rounding=ROUND_HALF_UP
    )


__all__ = [
    "calculate_cpl",
    "calculate_effective_like_count",
    "calculate_is_hit",
]
