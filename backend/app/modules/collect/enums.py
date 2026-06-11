"""U13 采集模块枚举。"""

from __future__ import annotations

from enum import Enum


class CrawlerPlatform(str, Enum):
    """采集平台（与 U12 CredentialPlatform 对齐）。"""

    QIANNIU = "千牛"
    WANXIANGTAI = "万相台"
    HUITUN = "灰豚"


class CrawlerStatus(str, Enum):
    """crawler_task 状态机。"""

    PENDING = "pending"
    ASSIGNED = "assigned"
    EXCHANGED = "exchanged"
    SUCCESS = "success"
    FAILED = "failed"


class DqSeverity(str, Enum):
    """数据质量严重度。"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class DqStatus(str, Enum):
    """数据质量异常处理状态。"""

    OPEN = "open"
    FIXED = "fixed"
    IGNORED = "ignored"


# platform → import source 映射（adapter source 键）
PLATFORM_SOURCE = {
    "千牛": "qianniu",
    "万相台": "wanxiangtai",
    "灰豚": "huitun",
}


__all__ = [
    "PLATFORM_SOURCE",
    "CrawlerPlatform",
    "CrawlerStatus",
    "DqSeverity",
    "DqStatus",
]
