"""U03 模块枚举定义。"""

from __future__ import annotations

from enum import Enum


class BloggerType(str, Enum):
    """博主类型。MVP 阶段 PR 手动选择，V1 / U10b 系统按粉丝量自动计算。"""

    AMATEUR = "素人"      # < 1k
    KOC = "KOC"           # 1k - 10k
    KOL = "KOL"           # 10k - 100w
    CELEBRITY = "明星"    # 100w+


class Platform(str, Enum):
    """博主所属平台。MVP 仅小红书，V1+ 扩展。"""

    XIAOHONGSHU = "小红书"
    DOUYIN = "抖音"
    KUAISHOU = "快手"
    BILIBILI = "B站"


class GenderTarget(str, Enum):
    """博主受众性别。"""

    FEMALE = "女性"
    MALE = "男性"
    UNISEX = "中性"
