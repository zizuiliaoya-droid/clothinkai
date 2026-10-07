"""新建商品的内部编码由系统生成（补充 3，FR-9，设计 §11.2）。

编码只用来做引用键（报表、链接、推广都按它找商品），界面上不再显示（补充 2）。规则：

- 单品（1 个成员）：编码 = 款号；被占用依次试 ``款号-2`` … ``款号-99``
- 套装（≥ 2 个成员）：``SUIT-`` + 成员款号升序、``_`` 连接；超过 64 字、或含 9 位以上连续数字
  （看着像千牛 ID）改用 ``SUIT-{yyyymmdd}-{6 位十六进制}``；被占用追加 ``-2`` … ``-99``
- 以上都占满 → ``G-{yyyymmdd}-{8 位十六进制}``，最多试 5 次，仍冲突 → 409

占用判断含已软删的商品（``code_exists``）。输入只有成员款号、不读链接表，所以编码里不会有平台 ID。
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol

from app.modules.product.goods_models import GoodsMain
from app.modules.product.goods_schemas import goods_display_name

GOODS_CODE_MAX_LEN = 64
SUFFIX_MAX = 99
FALLBACK_ATTEMPTS = 5
SUIT_PREFIX = "SUIT-"
_PLATFORM_ID_LIKE = re.compile(r"\d{9,}")
_BUSINESS_TZ = timezone(timedelta(hours=8))  # 业务日期按北京时间

NOTICE_OCCUPIED = "该款已有商品「{name}」，已为新商品另行生成内部编码"
NOTICE_OCCUPIED_BY_DELETED = "该款号对应的编码曾被已删除的商品使用，已另行生成"


class GoodsCodeExhaustedError(Exception):
    """候选编码全部被占用（几乎不可能）；服务层转成 409 ``GOODS_CODE_CONFLICT``。"""


class GoodsCodeRepo(Protocol):
    async def code_exists(self, goods_code: str) -> bool: ...

    async def get_by_code(
        self, goods_code: str, *, include_deleted: bool = False
    ) -> GoodsMain | None: ...


def _today() -> str:
    return datetime.now(_BUSINESS_TZ).strftime("%Y%m%d")


def _random_hex(nbytes: int) -> str:
    return secrets.token_hex(nbytes)


def suit_base_code(member_codes: Sequence[str]) -> str:
    """套装编码的基础形态：成员款号组合，过长或像平台 ID 时改流水码。"""
    combined = SUIT_PREFIX + "_".join(sorted(member_codes))
    if len(combined) > GOODS_CODE_MAX_LEN or _PLATFORM_ID_LIKE.search(combined):
        return f"{SUIT_PREFIX}{_today()}-{_random_hex(3)}"
    return combined


def _with_suffixes(base: str) -> Iterator[str]:
    yield base
    for n in range(2, SUFFIX_MAX + 1):
        candidate = f"{base}-{n}"
        if len(candidate) <= GOODS_CODE_MAX_LEN:
            yield candidate


async def _occupied_notice(repo: GoodsCodeRepo, code: str) -> str:
    holder = await repo.get_by_code(code, include_deleted=True)
    if holder is None or holder.is_deleted:
        return NOTICE_OCCUPIED_BY_DELETED
    return NOTICE_OCCUPIED.format(name=goods_display_name(holder.goods_title, holder.short_name))


async def generate_goods_code(
    repo: GoodsCodeRepo, member_codes: list[str]
) -> tuple[str, str | None]:
    """返回 (新编码, 给界面的提示)。提示里不出现任何编码；套装与没被占用的单品没有提示。"""
    if not member_codes:
        raise ValueError("member_codes 不能为空")
    single = len(member_codes) == 1
    base = member_codes[0] if single else suit_base_code(member_codes)
    notice: str | None = None
    for candidate in _with_suffixes(base):
        if not await repo.code_exists(candidate):
            return candidate, notice
        if single and notice is None:
            notice = await _occupied_notice(repo, base)
    for _ in range(FALLBACK_ATTEMPTS):
        candidate = f"G-{_today()}-{_random_hex(4)}"
        if not await repo.code_exists(candidate):
            return candidate, notice
    raise GoodsCodeExhaustedError(base)


__all__ = [
    "GOODS_CODE_MAX_LEN",
    "NOTICE_OCCUPIED",
    "NOTICE_OCCUPIED_BY_DELETED",
    "GoodsCodeExhaustedError",
    "generate_goods_code",
    "suit_base_code",
]
