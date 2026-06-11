"""TEMPORARY: System settings hardcoded for U04.

REMOVE AFTER V1 system_setting 单元 is implemented.

包含 U04 实时计算字段所需的系统设置常量：
- PLATFORM_LIKE_COEFFICIENT：平台点赞折算系数（EP05-S10）
- HIT_THRESHOLD_LIKE_COUNT：爆文阈值（EP05-S11）
- URGE_THRESHOLD_DAYS / IMPORTANT_THRESHOLD_DAYS：催发天数阈值（EP05-S06）

V1+ system_setting 单元落地后：
- 重写为 SystemSettingsService.get_promotion_settings()
- grep ``legacy_settings`` 替换全部引用
- 删除本文件
"""

from __future__ import annotations

from decimal import Decimal


PLATFORM_LIKE_COEFFICIENT: dict[str, Decimal] = {
    "小红书": Decimal("1.0"),
    "抖音": Decimal("0.1"),    # 抖音点赞 ÷ 10
    "快手": Decimal("0.1"),
    "B站": Decimal("1.0"),
}
"""平台点赞折算系数（EP05-S10）。"""


HIT_THRESHOLD_LIKE_COUNT: int = 1000
"""爆文阈值（EP05-S11）。like_count >= 此值标记 is_hit=true。"""


URGE_THRESHOLD_DAYS: int = 10
"""催发天数阈值（EP05-S06）。scheduled_publish_date - today > 10 天为 '档期内'。"""


IMPORTANT_THRESHOLD_DAYS: int = 3
"""重要催发天数阈值（EP05-S06）。scheduled_publish_date - today ≤ 3 天为 '重要催发'。"""


__all__ = [
    "HIT_THRESHOLD_LIKE_COUNT",
    "IMPORTANT_THRESHOLD_DAYS",
    "PLATFORM_LIKE_COEFFICIENT",
    "URGE_THRESHOLD_DAYS",
]
