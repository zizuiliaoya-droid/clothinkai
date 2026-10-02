"""TEMPORARY: System settings hardcoded for U04.

REMOVE AFTER V1 system_setting 单元 is implemented.

包含 U04 实时计算字段所需的系统设置常量：
- PLATFORM_LIKE_COEFFICIENT：平台点赞折算系数（EP05-S10）
- HIT_THRESHOLD_LIKE_COUNT：爆文阈值（EP05-S11）

催发天数阈值（原 URGE_THRESHOLD_DAYS / IMPORTANT_THRESHOLD_DAYS）已收进租户的
``urge_config`` 表，从 ``UrgeService.get_urge_thresholds`` 取 —— 别在这里再加回来：
写死的那份让后台改阈值只对企微扫描生效，推广列表和工作进度一直按 10 / 3 算。

V1+ system_setting 单元落地后：
- 重写为 SystemSettingsService.get_promotion_settings()
- grep ``legacy_settings`` 替换全部引用
- 删除本文件
"""

from __future__ import annotations

from decimal import Decimal

PLATFORM_LIKE_COEFFICIENT: dict[str, Decimal] = {
    "小红书": Decimal("1.0"),
    "抖音": Decimal("0.1"),  # 抖音点赞 ÷ 10
    "快手": Decimal("0.1"),
    "B站": Decimal("1.0"),
}
"""平台点赞折算系数（EP05-S10）。"""


HIT_THRESHOLD_LIKE_COUNT: int = 1000
"""爆文阈值（EP05-S11）。like_count >= 此值标记 is_hit=true。"""


__all__ = [
    "HIT_THRESHOLD_LIKE_COUNT",
    "PLATFORM_LIKE_COEFFICIENT",
]
