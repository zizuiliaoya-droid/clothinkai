"""款式图片相关的纯函数（8a-2 / 8a-4，设计 §7）。

- ``resolve_style_image``：款式图取值，一处实现（J9，§7.1）——已上传主图（私有桶签名 URL）>
  聚水潭外部链接 > 无
- ``normalize_external_image_url``：聚水潭导出「图片」列的外部链接校验（§7.4）
- ``image_stem``：批量传图按文件名匹配款号用的 stem（§7.3，前端 ``imageBatch.ts::imageStem`` 同口径）

外部链接只存不取：服务端在导入、保存、列表、展示的任何环节都**不请求**这个地址，
这里也只做字符串层面的校验。签名是本地计算（boto3 预签名不发请求）。
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from app.core.attachment import attachment_service

log = logging.getLogger(__name__)

EXTERNAL_IMAGE_URL_MAX_LEN = 1024
STYLE_IMAGE_SIGNED_URL_TTL = 3600
_ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True)
class StyleImage:
    url: str
    source: Literal["upload", "external"]


def resolve_style_image(
    main_image_key: str | None, external_image_url: str | None
) -> StyleImage | None:
    """款式图：已上传主图 → 私有桶签名 URL（1 小时）；签名失败只记 warning 并回落；
    否则外部链接；都没有 → None（前端显示「暂无主图」）。"""
    if main_image_key and attachment_service.is_configured:
        try:
            url = attachment_service.get_signed_url(
                "private", main_image_key, expires_in=STYLE_IMAGE_SIGNED_URL_TTL
            )
            return StyleImage(url=url, source="upload")
        except Exception:
            log.warning("style_image_sign_failed", extra={"key": main_image_key})
    if external_image_url:
        return StyleImage(url=external_image_url, source="external")
    return None


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


def image_stem(filename: str) -> str:
    """文件名 → 用来匹配款号的 stem：去目录（``/`` 与 ``\\``）、去最后一个扩展名、去首尾空白。

    ``a/b/ABC.jpg`` → ``ABC``；``x.tar.gz`` → ``x.tar``；没有扩展名的原样（去空白）。
    以点开头且没有别的点的（``.jpg``）当作没有扩展名，与前端同口径。
    """
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    dot = name.rfind(".")
    if dot > 0:
        name = name[:dot]
    return name.strip()


__all__ = [
    "EXTERNAL_IMAGE_URL_MAX_LEN",
    "STYLE_IMAGE_SIGNED_URL_TTL",
    "StyleImage",
    "image_stem",
    "normalize_external_image_url",
    "resolve_style_image",
]
