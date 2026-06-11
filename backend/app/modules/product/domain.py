"""U02 业务规则领域层。

仅做：
- 业务规则验证（不依赖 DB / Session）
- dict diff（计算变更字段）
- audit_safe_changes 转换（敏感值脱敏，与 NFR §5.3 一致）

不做：
- 持久化（由 repository.py）
- 业务编排（由 service.py）
- 字段写权限校验（由 service.py 调用 ``legacy_field_permissions``）
"""

from __future__ import annotations

from typing import Any

from app.modules.product.enums import SourcingType
from app.modules.product.exceptions import (
    InvalidPriceError,
    SourcingPriceMismatchError,
)
from app.modules.product.models import Sku, Style
from app.modules.product.schemas import SkuCreate, SkuUpdate, StyleUpdate

# ---------------------------------------------------------------------------
# 审计敏感字段配置（与 NFR §5.3 + business-rules BR-U02-30/31 对齐）
# ---------------------------------------------------------------------------


STYLE_SENSITIVE_FIELDS: frozenset[str] = frozenset({"style_code"})
"""Style 表写 audit_log 的字段白名单（BR-U02-30）。"""

SKU_SENSITIVE_FIELDS: frozenset[str] = frozenset(
    {"sku_code", "cost_price", "purchase_price", "base_price", "sourcing_type"}
)
"""SKU 表写 audit_log 的字段白名单（BR-U02-31）。"""

SKU_SENSITIVE_VALUE_FIELDS: frozenset[str] = frozenset(
    {"cost_price", "purchase_price"}
)
"""SKU 表 audit_log 不存历史值的字段（仅记 ``*_changed: true``）。"""


# ---------------------------------------------------------------------------
# 业务规则验证
# ---------------------------------------------------------------------------


def validate_sku_sourcing_price(
    payload: SkuCreate | SkuUpdate,
    *,
    base: Sku | None = None,
) -> None:
    """BR-U02-13: sourcing_type 与价格字段一致性。

    - 自产 → 至少有 cost_price
    - 外采 → 至少有 purchase_price
    - 混合 → 两者都可填（也可都为 NULL，便于历史导入）
    """
    sourcing = _resolve_sourcing_type(payload, base)
    cost = _resolve_value(payload, base, "cost_price")
    purchase = _resolve_value(payload, base, "purchase_price")

    if sourcing == SourcingType.SELF_PRODUCED and cost is None:
        raise SourcingPriceMismatchError(
            "自产 SKU 必须填写 cost_price",
            details={"sourcing_type": "自产"},
        )
    if sourcing == SourcingType.EXTERNAL_PURCHASE and purchase is None:
        raise SourcingPriceMismatchError(
            "外采 SKU 必须填写 purchase_price",
            details={"sourcing_type": "外采"},
        )


def validate_sku_prices(payload: SkuCreate | SkuUpdate) -> None:
    """BR-U02-14 / BR-U02-15: 价格非负 + DECIMAL(10,2) 精度。

    Pydantic 已在 schema 层做 ``ge=0`` + ``max_digits=10`` + ``decimal_places=2``，
    此处仅作为 service 层二次防线（防绕过 Pydantic 直接构造 dict）。
    """
    for field in ("cost_price", "purchase_price", "base_price"):
        value = getattr(payload, field, None)
        if value is None:
            continue
        if value < 0:
            raise InvalidPriceError(
                f"{field} 不能为负数",
                details={"field": field, "value": str(value)},
            )


# ---------------------------------------------------------------------------
# dict diff
# ---------------------------------------------------------------------------


def compute_style_changes(
    style: Style, payload: StyleUpdate
) -> dict[str, dict[str, Any]]:
    """对比当前 ORM 实例与 payload，返回变更字段的 ``{before, after}`` 字典。

    仅包含 ``payload.model_fields_set`` 中显式设置的字段（PATCH 语义）。
    """
    changes: dict[str, dict[str, Any]] = {}
    fields = payload.model_fields_set

    for field in fields:
        new = getattr(payload, field)
        old = getattr(style, field, None)
        if old != new:
            changes[field] = {"before": _serialize(old), "after": _serialize(new)}
    return changes


def compute_sku_changes(
    sku: Sku, payload: SkuUpdate
) -> dict[str, dict[str, Any]]:
    """同 ``compute_style_changes``，对 SKU。"""
    changes: dict[str, dict[str, Any]] = {}
    fields = payload.model_fields_set

    for field in fields:
        new = getattr(payload, field)
        old = getattr(sku, field, None)
        if old != new:
            changes[field] = {"before": _serialize(old), "after": _serialize(new)}
    return changes


# ---------------------------------------------------------------------------
# audit_safe_changes 转换
# ---------------------------------------------------------------------------


def build_style_audit_changes(
    changes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """BR-U02-30 + BR-U02-32: Style 仅敏感字段写 audit；未变更不写。"""
    return {k: v for k, v in changes.items() if k in STYLE_SENSITIVE_FIELDS}


def build_sku_audit_changes(
    changes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """BR-U02-31 + NFR §5.3: SKU 敏感字段写 audit，敏感值脱敏。

    - ``cost_price`` / ``purchase_price`` → 仅 ``cost_price_changed: true`` 标记
    - ``base_price`` / ``sku_code`` / ``sourcing_type`` → 正常 before/after
    """
    audit_safe: dict[str, Any] = {}
    for field, diff in changes.items():
        if field not in SKU_SENSITIVE_FIELDS:
            continue
        if field in SKU_SENSITIVE_VALUE_FIELDS:
            audit_safe[f"{field}_changed"] = True
        else:
            audit_safe[field] = diff
    return audit_safe


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------


def _resolve_sourcing_type(
    payload: SkuCreate | SkuUpdate, base: Sku | None
) -> SourcingType:
    """优先取 payload.sourcing_type；若 None 则取 base."""
    if "sourcing_type" in payload.model_fields_set:
        v = payload.sourcing_type
        if v is not None:
            return SourcingType(v) if not isinstance(v, SourcingType) else v
    if base is not None:
        return SourcingType(base.sourcing_type)
    return SourcingType.SELF_PRODUCED


def _resolve_value(
    payload: SkuCreate | SkuUpdate, base: Sku | None, field: str
) -> Any:
    """优先取 payload.<field>；若未显式设置则取 base."""
    if field in payload.model_fields_set:
        return getattr(payload, field)
    if base is not None:
        return getattr(base, field)
    return None


def _serialize(value: Any) -> Any:
    """ORM / Pydantic 字段转为 JSON 可序列化值（datetime / Decimal / UUID）。"""
    from datetime import date, datetime
    from decimal import Decimal
    from uuid import UUID

    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    return value


__all__ = [
    "SKU_SENSITIVE_FIELDS",
    "SKU_SENSITIVE_VALUE_FIELDS",
    "STYLE_SENSITIVE_FIELDS",
    "build_sku_audit_changes",
    "build_style_audit_changes",
    "compute_sku_changes",
    "compute_style_changes",
    "validate_sku_prices",
    "validate_sku_sourcing_price",
]
