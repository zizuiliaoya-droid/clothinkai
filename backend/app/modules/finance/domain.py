"""U05 业务规则领域层。

仅做：
- 业务规则验证（不依赖 DB / Session）
- dict diff（计算变更字段）
- audit_safe_changes 转换（敏感值脱敏，与 NFR §4 + BR-U05-50 一致）
- format_settlement_no（业务键格式化，与 U04 format_internal_code 同模式）

不做：
- 持久化（由 repository.py）
- 业务编排（由 service.py）
- 字段写权限校验（由 service.py 调用 ``legacy_field_permissions``）
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.modules.finance.models import Settlement
from app.modules.finance.schemas import (
    SettlementExtraItemCreateRequest,
    SettlementPaymentAmountRequest,
    SettlementPaymentProofRequest,
    SettlementReviewRequest,
)

# ---------------------------------------------------------------------------
# 审计敏感字段配置（与 BR-U05-50 + nfr-design §4 对齐）
# ---------------------------------------------------------------------------


SETTLEMENT_SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {
        "settlement_no",
        "amount",
        "total_amount",
        "payment_amount",
        "payment_date",
        "settlement_status",
        "review_action",
        "payment_proof_attachment_id",
    }
)
"""Settlement 表写 audit_log 的字段白名单。"""


SETTLEMENT_SENSITIVE_VALUE_FIELDS: frozenset[str] = frozenset(
    {"amount", "total_amount", "payment_amount"}
)
"""Settlement 表 audit_log 不存历史值的字段（仅记 ``*_changed: true`` 标记）。

与 U02 BR-U02-31 / U04 BR-U04-40 同模式（金额字段仅记 changed 标记）。
"""

ATTACHMENT_ID_AUDIT_FIELDS: frozenset[str] = frozenset(
    {"payment_proof_attachment_id"}
)
"""payment_proof_attachment_id 写 audit 时仅记 ``attachment_id_changed: true``
（避免暴露 attachment 内部 ID 与 R2 路径关联，FB3+FB4 强化）。"""


# ---------------------------------------------------------------------------
# settlement_no 格式化（BR-U05-01，复用 U04 模式）
# ---------------------------------------------------------------------------


def format_settlement_no(
    *,
    tenant_code: str,
    date_key: date,
    sequence: int,
) -> str:
    """生成 settlement_no = ``<tenant_prefix>S<yyMMdd><0001>``.

    与 U04 format_internal_code 完全相同模式，仅在中间加字面 ``S`` 标识 settlement。

    Args:
        tenant_code: tenant.code，取前 2 字符大写；不足补 ``X``。
        date_key: SettlementRequested.requested_at::date（不是 promotion.cooperation_date）。
        sequence: 1..9999；超过 9999 由 service 层抛 SequenceOverflowError。
    """
    prefix = (tenant_code or "").upper()[:2]
    if len(prefix) < 2:
        prefix = (prefix + "XX")[:2]
    date_part = date_key.strftime("%y%m%d")
    return f"{prefix}S{date_part}{sequence:04d}"


# ---------------------------------------------------------------------------
# dict diff
# ---------------------------------------------------------------------------


_PayloadTypes = (
    SettlementReviewRequest
    | SettlementPaymentAmountRequest
    | SettlementPaymentProofRequest
    | SettlementExtraItemCreateRequest
)


def compute_settlement_changes(
    settlement: Settlement,
    after_values: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """对比当前 ORM 实例与 after_values dict，返回变更字段的 ``{before, after}``。

    与 U04 模式一致，但 settlement 状态推进字段较多，由 service 层显式构造
    after_values（而非直接接受 Pydantic 入参，因为 Pydantic 不含 status / paid_by 等
    state-推进时的副字段）。
    """
    changes: dict[str, dict[str, Any]] = {}
    for field, new_value in after_values.items():
        old_value = getattr(settlement, field, None)
        if old_value != new_value:
            changes[field] = {
                "before": _serialize(old_value),
                "after": _serialize(new_value),
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
# audit_safe_changes 转换（FB3 + FB4 强化脱敏）
# ---------------------------------------------------------------------------


def build_settlement_audit_changes(
    changes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """BR-U05-50: Settlement 仅敏感字段写 audit；金额值脱敏 + attachment_id 脱敏.

    - ``amount`` / ``total_amount`` / ``payment_amount`` → 仅 ``*_changed: true`` 标记（FB3 一致）
    - ``payment_proof_attachment_id`` → 仅 ``attachment_id_changed: true`` 标记（FB3+FB4 强化）
    - ``settlement_no`` / ``payment_date`` / ``settlement_status`` / ``review_action`` → 正常 before/after
    - 其他字段（remark / note_title 等）→ 不写 audit
    """
    audit_safe: dict[str, Any] = {}
    for field, diff in changes.items():
        if field not in SETTLEMENT_SENSITIVE_FIELDS:
            continue
        if field in SETTLEMENT_SENSITIVE_VALUE_FIELDS:
            audit_safe[f"{field}_changed"] = True
        elif field in ATTACHMENT_ID_AUDIT_FIELDS:
            audit_safe["attachment_id_changed"] = True
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
    "ATTACHMENT_ID_AUDIT_FIELDS",
    "SETTLEMENT_SENSITIVE_FIELDS",
    "SETTLEMENT_SENSITIVE_VALUE_FIELDS",
    "build_settlement_audit_changes",
    "compute_settlement_changes",
    "compute_state_change",
    "format_settlement_no",
]
