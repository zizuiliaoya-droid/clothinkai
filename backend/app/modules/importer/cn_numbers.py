"""导入用的中文数值解析（8b §6.4）：灰豚等导出里「1.2w」「3.5万」「1亿」「-1,234」这类写法。

只做「原文 → Decimal」；取整、范围、金额校验留给调用方（粉丝数 ROUND_HALF_UP 取整、报价再过 ``compare.check_money``）。
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.modules.importer.compare import is_placeholder

# 去空白、去千分位之后整格全匹配；不认 k、%、科学计数法、「1.2w+」、区间
_NUMBER_RE = re.compile(r"^([+-]?(?:\d+(?:\.\d+)?|\.\d+))(w|W|万|亿)?$")
_STRIP_RE = re.compile(r"[\s,，]")  # str 模式下 \s 含 NBSP、全角空格
_UNIT_FACTOR = {
    "w": Decimal(10_000),
    "W": Decimal(10_000),
    "万": Decimal(10_000),
    "亿": Decimal(100_000_000),
}


def parse_cn_number(raw: object) -> Decimal | None:
    """None / 占位符 → None；能解析 → Decimal；否则 ``ValueError("数值格式不正确")``（信息里不带原值）。"""
    if isinstance(raw, bool):
        raise ValueError("数值格式不正确")
    if is_placeholder(raw):
        return None
    if isinstance(raw, int | float | Decimal):
        value = Decimal(str(raw))
        if not value.is_finite():
            raise ValueError("数值格式不正确")
        return value
    if not isinstance(raw, str):
        raise ValueError("数值格式不正确")
    match = _NUMBER_RE.fullmatch(_STRIP_RE.sub("", raw))
    if match is None:
        raise ValueError("数值格式不正确")
    value = Decimal(match.group(1))
    unit = match.group(2)
    return value * _UNIT_FACTOR[unit] if unit else value
