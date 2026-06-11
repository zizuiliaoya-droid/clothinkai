"""U12 凭据模块枚举。"""

from __future__ import annotations

from enum import Enum


class CredentialPlatform(str, Enum):
    """采集平台。"""

    QIANNIU = "千牛"
    WANXIANGTAI = "万相台"
    HUITUN = "灰豚"


class CredentialStatus(str, Enum):
    """凭据状态。"""

    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


__all__ = ["CredentialPlatform", "CredentialStatus"]
