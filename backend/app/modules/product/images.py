"""款式图片相关的纯函数（8a-2 / 8a-4，设计 §7）。

- ``normalize_external_image_url``：聚水潭导出「图片」列的外部链接校验（§7.4）

外部链接只存不取：服务端在导入、保存、列表、展示的任何环节都**不请求**这个地址，
这里也只做字符串层面的校验。
"""

from __future__ import annotations

import unicodedata
from urllib.parse import urlsplit

EXTERNAL_IMAGE_URL_MAX_LEN = 1024
_ALLOWED_SCHEMES = frozenset({"http", "https"})


def normalize_external_image_url(raw: object) -> str | None:
    """外部图片链接：合法返回去首尾空白后的地址，否则 None。

    规则：去首尾空白；长度 ≤ 1024；不含空白与控制字符；``urlsplit`` 后 scheme 是
    http / https（不区分大小写）且 host 非空。``javascript:``、``data:``、``ftp://`` 一律不收。
    """
    if not isinstance(raw, str):
        return None
    url = raw.strip()
    if not url or len(url) > EXTERNAL_IMAGE_URL_MAX_LEN:
        return None
    if any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:  # 如 IPv6 方括号不配对
        return None
    if parts.scheme.lower() not in _ALLOWED_SCHEMES or not parts.hostname:
        return None
    return url


__all__ = ["EXTERNAL_IMAGE_URL_MAX_LEN", "normalize_external_image_url"]
