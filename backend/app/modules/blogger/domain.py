"""U03 业务规则领域层。

仅做：
- 业务规则验证（不依赖 DB / Session）
- dict diff（计算变更字段）
- audit_safe_changes 转换（敏感值脱敏，与 NFR §5.3 + BR-U03-30 一致）
"""

from __future__ import annotations

from typing import Any

from app.modules.blogger.models import Blogger
from app.modules.blogger.schemas import BloggerCreate, BloggerUpdate

# ---------------------------------------------------------------------------
# 审计敏感字段配置（BR-U03-30）
# ---------------------------------------------------------------------------


BLOGGER_SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {"xiaohongshu_id", "nickname", "quote", "wechat", "phone"}
)
"""Blogger 表写 audit_log 的字段白名单（BR-U03-30）。"""

BLOGGER_SENSITIVE_VALUE_FIELDS: frozenset[str] = frozenset(
    {"quote", "wechat", "phone"}
)
"""Blogger 表 audit_log 不存历史值的字段（仅记 ``*_changed: true`` 标记）。"""


# ---------------------------------------------------------------------------
# dict diff
# ---------------------------------------------------------------------------


def compute_blogger_changes(
    blogger: Blogger, payload: BloggerUpdate
) -> dict[str, dict[str, Any]]:
    """对比 ORM 实例与 payload，返回变更字段的 ``{before, after}`` 字典。

    仅包含 ``payload.model_fields_set`` 中显式设置的字段（PATCH 语义）。
    """
    changes: dict[str, dict[str, Any]] = {}
    for field in payload.model_fields_set:
        new = getattr(payload, field)
        old = getattr(blogger, field, None)
        if old != new:
            changes[field] = {
                "before": _serialize(old),
                "after": _serialize(new),
            }
    return changes


# ---------------------------------------------------------------------------
# audit_safe_changes 转换（敏感值脱敏）
# ---------------------------------------------------------------------------


def build_blogger_audit_changes(
    changes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """BR-U03-30: Blogger 仅敏感字段写 audit；敏感值脱敏。

    - ``quote`` / ``wechat`` / ``phone`` → 仅 ``*_changed: true`` 标记
    - ``xiaohongshu_id`` / ``nickname`` → 正常 before/after
    - 其他字段（tags / remark / blogger_type / follower_count 等）→ 不写 audit
    """
    audit_safe: dict[str, Any] = {}
    for field, diff in changes.items():
        if field not in BLOGGER_SENSITIVE_FIELDS:
            continue
        if field in BLOGGER_SENSITIVE_VALUE_FIELDS:
            audit_safe[f"{field}_changed"] = True
        else:
            audit_safe[field] = diff
    return audit_safe


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _serialize(value: Any) -> Any:
    """字段值转为 JSON 可序列化（datetime / Decimal / UUID / Enum）。"""
    from datetime import date, datetime
    from decimal import Decimal
    from enum import Enum
    from uuid import UUID

    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    return value


__all__ = [
    "BLOGGER_SENSITIVE_FIELDS",
    "BLOGGER_SENSITIVE_VALUE_FIELDS",
    "build_blogger_audit_changes",
    "compute_blogger_changes",
]
