"""U11 博主智能标签阈值常量。

集中 5 个阈值，便于后续调参 / 迁移到 system_setting。

- blogger_type 分级：粉丝量阈值（KOL / KOC / 素人）
- 假号判定：read_like_ratio ≤ FAKE_RATIO_THRESHOLD
- 质量标签：avg CPL ≤ HIGH_CPL_THRESHOLD → "高性价比"；hit_rate ≥ HIT_RATE_THRESHOLD → "带货型"
"""

from __future__ import annotations

from decimal import Decimal

# blogger_type 粉丝量阈值
FOLLOWER_KOC_MIN = 10_000
FOLLOWER_KOL_MIN = 100_000

# 假号嫌疑：点赞/阅读比 ≤ 1% 视为异常低互动
FAKE_RATIO_THRESHOLD = Decimal("0.01")

# 质量标签阈值
HIGH_CPL_THRESHOLD = Decimal("5.00")  # 平均单赞成本 ≤ 5 元 → 高性价比
HIT_RATE_THRESHOLD = Decimal("0.20")  # 爆文率 ≥ 20% → 带货型

# 质量标签字面量
TAG_HIGH_VALUE = "高性价比"
TAG_BESTSELLER = "带货型"

# 质量聚合截断（防超大历史拖慢）
QUALITY_AGG_LIMIT = 1000


__all__ = [
    "FAKE_RATIO_THRESHOLD",
    "FOLLOWER_KOC_MIN",
    "FOLLOWER_KOL_MIN",
    "HIGH_CPL_THRESHOLD",
    "HIT_RATE_THRESHOLD",
    "QUALITY_AGG_LIMIT",
    "TAG_BESTSELLER",
    "TAG_HIGH_VALUE",
]
