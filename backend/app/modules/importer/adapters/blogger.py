"""U06c 博主导入适配器（BloggerImportAdapter）。

按 nfr-design-patterns.md P-U06c-01 实现：一行 = 一个 Blogger（单实体）。

关键设计：
- **不经 U03 Service**（Service 自带 commit/audit/字段权限，与 runner per-row 事务边界 FB-C 冲突，
  且 Celery worker 无 HTTP User）→ 直接用 BloggerRepository
- **不自行 commit**：复用 runner 传入的 session（runner 持有 per-row 事务 + SET LOCAL，NF-1）
- **单次 upsert**：BloggerRepository.upsert_atomic（ON CONFLICT xiaohongshu_id，复用 U03）
- 多类型解析：list（标签 → JSONB 数组，_split_tags）+ int（follower_count）+ Decimal（quote，禁 float）
- platform 空 → 显式传 "小红书"（防 ON CONFLICT UPDATE 路径用空覆盖既有值）
- mapping=None 回退内置默认映射（domain-entities §4）
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.repository import BloggerRepository
from app.modules.importer.registry import ImportAdapterRegistry

if TYPE_CHECKING:
    from app.modules.importer.models import FieldMapping

log = logging.getLogger(__name__)

# 标签分隔符（中英文分号/逗号）
_TAG_SEP = re.compile(r"[;；,，]")

# 内置默认映射（mapping=None 回退；中文表头 → 目标字段）
_DEFAULT_COLUMNS: list[dict[str, Any]] = [
    {"source_col": "小红书ID", "target_field": "xiaohongshu_id", "type": "str"},
    {"source_col": "昵称", "target_field": "nickname", "type": "str"},
    {"source_col": "平台", "target_field": "platform", "type": "str"},
    {"source_col": "微信", "target_field": "wechat", "type": "str"},
    {"source_col": "手机号", "target_field": "phone", "type": "str"},
    {"source_col": "粉丝数", "target_field": "follower_count", "type": "int"},
    {"source_col": "博主类型", "target_field": "blogger_type", "type": "str"},
    {"source_col": "性别投放", "target_field": "gender_target", "type": "str"},
    {"source_col": "类目标签", "target_field": "category_tags", "type": "list"},
    {"source_col": "质量标签", "target_field": "quality_tags", "type": "list"},
    {"source_col": "报价", "target_field": "quote", "type": "decimal"},
    {"source_col": "合作历史", "target_field": "cooperation_history", "type": "str"},
    {"source_col": "备注", "target_field": "remark", "type": "str"},
]

_REQUIRED: tuple[tuple[str, str], ...] = (
    ("xiaohongshu_id", "小红书ID"),
    ("nickname", "昵称"),
)
_MAX_LEN: tuple[tuple[str, int], ...] = (
    ("xiaohongshu_id", 64),
    ("nickname", 128),
    ("wechat", 64),
    ("phone", 32),
    ("platform", 16),
    ("blogger_type", 16),
    ("gender_target", 16),
)


def _split_tags(raw: Any) -> list[str]:
    """分隔字符串（;；,，）→ 拆分 + strip + 去空 → list。空 → []。"""
    if raw is None or str(raw).strip() == "":
        return []
    return [t.strip() for t in _TAG_SEP.split(str(raw)) if t.strip()]


def _to_int(raw: Any) -> int | str | None:
    """去千分位 + int。非法值保留原串供 validate。空 → None。"""
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = str(raw).replace(",", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        return str(raw)


def _to_decimal(raw: Any) -> Decimal | str | None:
    """去千分位 + Decimal（禁 float）。非法值保留原串。空 → None。"""
    if raw is None or str(raw).strip() == "":
        return None
    cleaned = str(raw).replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return str(raw)


class BloggerImportAdapter:
    """manual_blogger 导入适配器（一行 → 一个 Blogger upsert）。"""

    source: str = "manual_blogger"
    target_table: str = "blogger"

    # ----------------------- parse_row（纯函数）----------------------- #

    def parse_row(
        self, row: dict[str, Any], mapping: "FieldMapping | None"
    ) -> dict[str, Any]:
        """按 mapping（或内置默认）映射表头 + 多类型转换。"""
        if mapping is not None:
            columns = mapping.mapping_config.get("columns", _DEFAULT_COLUMNS)
        else:
            columns = _DEFAULT_COLUMNS

        parsed: dict[str, Any] = {}
        for col in columns:
            raw = row.get(col["source_col"])
            target = col["target_field"]
            col_type = col.get("type", "str")
            if col_type == "list":
                parsed[target] = _split_tags(raw)
            elif col_type == "int":
                parsed[target] = _to_int(raw)
            elif col_type == "decimal":
                parsed[target] = _to_decimal(raw)
            else:
                parsed[target] = (
                    str(raw).strip() if raw not in (None, "") else None
                )
        return parsed

    # ----------------------- validate（纯函数）----------------------- #

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        """返回错误描述列表（空=通过）。"""
        errs: list[str] = []
        for field, label in _REQUIRED:
            if not parsed.get(field):
                errs.append(f"{label}不能为空")
        follower = parsed.get("follower_count")
        if follower is not None and (
            not isinstance(follower, int) or follower < 0
        ):
            errs.append("粉丝数必须为非负整数")
        quote = parsed.get("quote")
        if quote is not None and (not isinstance(quote, Decimal) or quote < 0):
            errs.append("报价必须为非负数字")
        for field, max_len in _MAX_LEN:
            value = parsed.get(field)
            if value and isinstance(value, str) and len(value) > max_len:
                errs.append(f"{field} 超过长度上限 {max_len}")
        return errs


    # ----------------------- upsert（复用 runner session，不 commit）----------------------- #

    async def upsert(
        self,
        parsed: dict[str, Any],
        *,
        session: AsyncSession,
        tenant_id: UUID,
        actor_id: UUID | None,
    ) -> tuple[UUID, bool]:
        """单次 BloggerRepository.upsert_atomic（ON CONFLICT xiaohongshu_id）。

        不自行 commit（runner 持有 per-row 事务边界，FB-C）。
        actor_id 不写业务表（U03 blogger 无 created_by，仅审计上下文）。
        """
        repo = BloggerRepository(session)
        values: dict[str, Any] = {
            "xiaohongshu_id": parsed["xiaohongshu_id"],
            "nickname": parsed["nickname"],
            # platform 空 → 显式默认 "小红书"（防 ON CONFLICT UPDATE 覆盖既有值）
            "platform": parsed.get("platform") or "小红书",
            "wechat": parsed.get("wechat"),
            "phone": parsed.get("phone"),
            "follower_count": parsed.get("follower_count"),
            "blogger_type": parsed.get("blogger_type"),
            "gender_target": parsed.get("gender_target"),
            "category_tags": parsed.get("category_tags") or [],
            "quality_tags": parsed.get("quality_tags") or [],
            "quote": parsed.get("quote"),
            "cooperation_history": parsed.get("cooperation_history"),
            "remark": parsed.get("remark"),
        }
        blogger, is_inserted = await repo.upsert_atomic(
            tenant_id=tenant_id, values=values
        )
        return blogger.id, is_inserted


def register() -> None:
    """注册到 ImportAdapterRegistry（由 register_import_adapters 双进程调用，NF-4）。"""
    ImportAdapterRegistry.register(BloggerImportAdapter())


__all__ = ["BloggerImportAdapter", "register"]
