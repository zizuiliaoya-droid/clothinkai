"""推广单收件三项（流程线 M1 / PR-2）。

- ``normalize_receiver_phone``：收件电话的唯一校验函数。推广单 PATCH / ``POST /`` / 推送仓库补填，
  以及谈款确认收货信息（N9，PR-3）都调它，不要另写一份
- ``retired_source_extra_keys``：``source_extra`` 里已经搬成 typed 列的键（M1），写入时拒收
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.modules.promotion.exceptions import InvalidReceiverPhoneError

RECEIVER_FIELDS: tuple[str, ...] = ("receiver_name", "receiver_phone", "receiver_address")
"""收件三项，顺序即报错 / 投影的顺序。字段规则 ``promotion.receiver_*``（4.6）。"""

RETIRED_SOURCE_EXTRA_KEYS: tuple[str, ...] = ("打单地址", "发货单号")
"""M1 起是 ``receiver_address`` / ``ship_waybill`` 列；超长的存量值原样留在 JSONB，只是不再接受写入。"""

# 只去空白（含全角空格）与短横；括号、点号这类不认，免得把备注当号码收下
_SEPARATORS = re.compile(r"[\s\-]+")
# 11 位手机号，1 开头；可带 +86 / 86 国家码，存时去掉
_MOBILE = re.compile(r"(?:\+?86)?(1\d{10})")
# 座机：0 开头，区号 + 号码共 10 ~ 12 位
_LANDLINE = re.compile(r"0\d{9,11}")


def normalize_receiver_phone(raw: str | None) -> str | None:
    """校验并规范化收件电话。

    - ``None`` 或去空白后是空串 → ``None``（清空）
    - 去掉空白与短横后：11 位手机号（可带 ``+86`` / ``86``，返回不带前缀的 11 位）或 0 开头 10 ~ 12 位座机
    - 其余 → ``InvalidReceiverPhoneError``（422 ``INVALID_RECEIVER_PHONE``）

    存规范化值：仓库对单、导出、按电话前缀搜索都只认一种写法。
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    compact = _SEPARATORS.sub("", stripped)
    mobile = _MOBILE.fullmatch(compact)
    if mobile is not None:
        return mobile.group(1)
    if _LANDLINE.fullmatch(compact) is not None:
        return compact
    raise InvalidReceiverPhoneError(
        "收件电话格式不对：应为 11 位手机号（可带 +86）或 0 开头的 10 ~ 12 位座机",
        details={"field": "receiver_phone"},
    )


def retired_source_extra_keys(keys: Iterable[str]) -> list[str]:
    """``keys`` 里带了哪些退役键，按 ``RETIRED_SOURCE_EXTRA_KEYS`` 的顺序返回。

    只看键在不在，不看值：传 null / 空串表示删键，同样不再接受（键本身退役了）。
    """
    present = set(keys)
    return [k for k in RETIRED_SOURCE_EXTRA_KEYS if k in present]


__all__ = [
    "RECEIVER_FIELDS",
    "RETIRED_SOURCE_EXTRA_KEYS",
    "normalize_receiver_phone",
    "retired_source_extra_keys",
]
