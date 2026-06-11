"""U02 SKU 业务规则 + audit 脱敏单元测试（domain.py）。

覆盖：
- BR-U02-13 sourcing_type 与价格一致性
- BR-U02-14 价格非负
- audit_safe_changes 转换：cost_price/purchase_price 仅记 *_changed 标记
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.product.domain import (
    SKU_SENSITIVE_FIELDS,
    SKU_SENSITIVE_VALUE_FIELDS,
    build_sku_audit_changes,
    compute_sku_changes,
    validate_sku_prices,
    validate_sku_sourcing_price,
)
from app.modules.product.enums import SourcingType
from app.modules.product.exceptions import (
    InvalidPriceError,
    SourcingPriceMismatchError,
)
from app.modules.product.models import Sku
from app.modules.product.schemas import SkuCreate, SkuUpdate


# ---------------------------------------------------------------------------
# BR-U02-13 sourcing_type 与价格一致性
# ---------------------------------------------------------------------------


class TestSourcingPriceConsistency:
    def test_self_produced_requires_cost(self) -> None:
        payload = SkuCreate(
            style_id=uuid4(),
            sku_code="SK001",
            color="红",
            size="M",
            cost_price=None,
            sourcing_type=SourcingType.SELF_PRODUCED,
        )
        with pytest.raises(SourcingPriceMismatchError):
            validate_sku_sourcing_price(payload)

    def test_self_produced_with_cost_ok(self) -> None:
        payload = SkuCreate(
            style_id=uuid4(),
            sku_code="SK001",
            color="红",
            size="M",
            cost_price=Decimal("100.00"),
            sourcing_type=SourcingType.SELF_PRODUCED,
        )
        validate_sku_sourcing_price(payload)  # 不抛异常

    def test_external_purchase_requires_purchase_price(self) -> None:
        payload = SkuCreate(
            style_id=uuid4(),
            sku_code="SK001",
            color="红",
            size="M",
            purchase_price=None,
            sourcing_type=SourcingType.EXTERNAL_PURCHASE,
        )
        with pytest.raises(SourcingPriceMismatchError):
            validate_sku_sourcing_price(payload)

    def test_external_purchase_with_purchase_ok(self) -> None:
        payload = SkuCreate(
            style_id=uuid4(),
            sku_code="SK001",
            color="红",
            size="M",
            purchase_price=Decimal("80.00"),
            sourcing_type=SourcingType.EXTERNAL_PURCHASE,
        )
        validate_sku_sourcing_price(payload)

    def test_mixed_allows_all_null(self) -> None:
        """混合允许两个价格字段都为 NULL（便于历史导入）."""
        payload = SkuCreate(
            style_id=uuid4(),
            sku_code="SK001",
            color="红",
            size="M",
            sourcing_type=SourcingType.MIXED,
        )
        validate_sku_sourcing_price(payload)


# ---------------------------------------------------------------------------
# BR-U02-14 价格非负
# ---------------------------------------------------------------------------


class TestPriceNonNegative:
    def test_negative_cost_raises(self) -> None:
        # Pydantic 已防止负数，service 层二次检查走 dict 强制
        # 这里通过 model_construct 跳过 Pydantic 验证
        payload = SkuUpdate.model_construct(
            cost_price=Decimal("-1.00"),
        )
        # 模拟 model_fields_set
        payload.__pydantic_fields_set__ = {"cost_price"}  # type: ignore[attr-defined]
        with pytest.raises(InvalidPriceError):
            validate_sku_prices(payload)

    def test_zero_cost_ok(self) -> None:
        payload = SkuCreate(
            style_id=uuid4(),
            sku_code="SK001",
            color="红",
            size="M",
            cost_price=Decimal("0.00"),
            sourcing_type=SourcingType.SELF_PRODUCED,
        )
        validate_sku_prices(payload)


# ---------------------------------------------------------------------------
# audit_safe_changes 脱敏（NFR §5.3 + BR-U02-31）
# ---------------------------------------------------------------------------


class TestAuditSafeChanges:
    def test_sensitive_value_fields_redacted(self) -> None:
        changes = {
            "cost_price": {"before": "100.00", "after": "120.00"},
            "purchase_price": {"before": "80.00", "after": "85.00"},
            "sku_code": {"before": "SK001", "after": "SK002"},
            "sourcing_type": {"before": "自产", "after": "混合"},
            "base_price": {"before": "200.00", "after": "210.00"},
        }
        audit = build_sku_audit_changes(changes)

        # 敏感值字段：仅记标记
        assert audit["cost_price_changed"] is True
        assert audit["purchase_price_changed"] is True
        assert "cost_price" not in audit
        assert "purchase_price" not in audit

        # 非敏感值：正常 before/after
        assert audit["sku_code"] == {"before": "SK001", "after": "SK002"}
        assert audit["sourcing_type"] == {"before": "自产", "after": "混合"}
        assert audit["base_price"] == {"before": "200.00", "after": "210.00"}

    def test_non_sensitive_excluded(self) -> None:
        changes = {
            "color": {"before": "红", "after": "蓝"},
            "size": {"before": "M", "after": "L"},
        }
        audit = build_sku_audit_changes(changes)
        assert audit == {}

    def test_constants_well_defined(self) -> None:
        assert SKU_SENSITIVE_FIELDS >= SKU_SENSITIVE_VALUE_FIELDS
        assert "cost_price" in SKU_SENSITIVE_VALUE_FIELDS
        assert "purchase_price" in SKU_SENSITIVE_VALUE_FIELDS
        assert "base_price" in SKU_SENSITIVE_FIELDS
        assert "base_price" not in SKU_SENSITIVE_VALUE_FIELDS


# ---------------------------------------------------------------------------
# compute_sku_changes（dict diff）
# ---------------------------------------------------------------------------


class TestComputeSkuChanges:
    def test_no_changes_returns_empty(self) -> None:
        sku = Sku(
            tenant_id=uuid4(),
            style_id=uuid4(),
            sku_code="SK001",
            color="红",
            size="M",
            cost_price=Decimal("100.00"),
            sourcing_type="自产",
            is_active=True,
            is_deleted=False,
        )
        payload = SkuUpdate(color="红")  # 同值
        changes = compute_sku_changes(sku, payload)
        assert changes == {}

    def test_cost_price_change_detected(self) -> None:
        sku = Sku(
            tenant_id=uuid4(),
            style_id=uuid4(),
            sku_code="SK001",
            color="红",
            size="M",
            cost_price=Decimal("100.00"),
            sourcing_type="自产",
            is_active=True,
            is_deleted=False,
        )
        payload = SkuUpdate(cost_price=Decimal("120.00"))
        changes = compute_sku_changes(sku, payload)
        assert "cost_price" in changes
        assert changes["cost_price"]["before"] == "100.00"
        assert changes["cost_price"]["after"] == "120.00"

    def test_only_set_fields_in_diff(self) -> None:
        """未在 payload 显式设置的字段不出现在 changes（PATCH 语义）."""
        sku = Sku(
            tenant_id=uuid4(),
            style_id=uuid4(),
            sku_code="SK001",
            color="红",
            size="M",
            cost_price=Decimal("100.00"),
            sourcing_type="自产",
            is_active=True,
            is_deleted=False,
        )
        payload = SkuUpdate(color="蓝")  # 仅设 color
        changes = compute_sku_changes(sku, payload)
        assert set(changes.keys()) == {"color"}
