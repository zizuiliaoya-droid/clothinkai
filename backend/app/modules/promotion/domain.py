"""U04 业务规则领域层。

仅做：
- 业务规则验证（不依赖 DB / Session）
- dict diff（计算变更字段）
- audit_safe_changes 转换（敏感值脱敏，与 NFR 设计 + BR-U04-40 一致）
- internal_code 格式化

不做：
- 持久化（由 repository.py）
- 业务编排（由 service.py）
- 字段写权限校验（由 service.py 调用 ``legacy_field_permissions``）
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.modules.promotion.models import Promotion
from app.modules.promotion.schemas import PromotionUpdate

# ---------------------------------------------------------------------------
# 审计敏感字段配置（与 BR-U04-40 + nfr-design §5 对齐）
# ---------------------------------------------------------------------------


PROMOTION_SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "internal_code",
        "publish_url",
        "quote_amount",
        "cost_snapshot",
        "publish_status",
        "recall_status",
        "settlement_status",
    }
)
"""Promotion 表写 audit_log 的字段白名单。"""


PROMOTION_SENSITIVE_VALUE_FIELDS: frozenset[str] = frozenset(
    {"quote_amount", "cost_snapshot"}
)
"""Promotion 表 audit_log 不存历史值的字段（仅记 ``*_changed: true`` 标记）。

与 U02 BR-U02-31 同模式（cost_price / purchase_price 仅记 changed 标记）。
"""


# ---------------------------------------------------------------------------
# internal_code 格式化（BR-U04-01）
# ---------------------------------------------------------------------------


def format_internal_code(
    *,
    tenant_code: str,
    cooperation_date: date,
    sequence: int,
) -> str:
    """生成 internal_code = ``<tenant_prefix><yyMMdd><0001>``。

    Args:
        tenant_code: tenant.code，取前 2 字符大写；不足补 ``X``。
        cooperation_date: 合作日期（不是 created_at）。
        sequence: 1..9999；超过 9999 由 service 层抛 SequenceOverflowError。
    """
    prefix = (tenant_code or "").upper()[:2]
    if len(prefix) < 2:
        prefix = (prefix + "XX")[:2]
    date_part = cooperation_date.strftime("%y%m%d")
    return f"{prefix}{date_part}{sequence:04d}"


# ---------------------------------------------------------------------------
# dict diff
# ---------------------------------------------------------------------------


def compute_promotion_changes(
    promotion: Promotion, payload: PromotionUpdate
) -> dict[str, dict[str, Any]]:
    """对比当前 ORM 实例与 payload，返回变更字段的 ``{before, after}`` 字典。

    仅包含 ``payload.model_fields_set`` 中显式设置的字段（PATCH 语义）。
    """
    changes: dict[str, dict[str, Any]] = {}
    for field in payload.model_fields_set:
        new = getattr(payload, field)
        old = getattr(promotion, field, None)
        if old != new:
            changes[field] = {
                "before": _serialize(old),
                "after": _serialize(new),
            }
    return changes


def compute_state_change(
    *,
    field: str,
    before: str,
    after: str,
) -> dict[str, dict[str, Any]]:
    """状态机推进的单字段变更（用于 audit）。"""
    if before == after:
        return {}
    return {field: {"before": before, "after": after}}


# ---------------------------------------------------------------------------
# audit_safe_changes 转换
# ---------------------------------------------------------------------------


def build_promotion_audit_changes(
    changes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """BR-U04-40 + NFR §5: Promotion 仅敏感字段写 audit；敏感值脱敏。

    - ``quote_amount`` / ``cost_snapshot`` → 仅 ``*_changed: true`` 标记
    - ``internal_code`` / ``publish_url`` / 三个状态字段 → 正常 before/after
    - 其他字段（platform / scheduled_publish_date / remark 等）→ 不写 audit
    """
    audit_safe: dict[str, Any] = {}
    for field, diff in changes.items():
        if field not in PROMOTION_SENSITIVE_FIELDS:
            continue
        if field in PROMOTION_SENSITIVE_VALUE_FIELDS:
            audit_safe[f"{field}_changed"] = True
        else:
            audit_safe[field] = diff
    return audit_safe


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _serialize(value: Any) -> Any:
    """ORM / Pydantic 字段转为 JSON 可序列化值（datetime / Decimal / UUID / Enum）。"""
    from datetime import date as _date
    from datetime import datetime as _datetime
    from decimal import Decimal
    from enum import Enum
    from uuid import UUID

    if value is None:
        return None
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (_datetime, _date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    return value


__all__ = [
    "PROMOTION_SENSITIVE_FIELDS",
    "PROMOTION_SENSITIVE_VALUE_FIELDS",
    "build_promotion_audit_changes",
    "compute_promotion_changes",
    "compute_state_change",
    "format_internal_code",
]
