"""U04 promotion domain.py 单元测试。

覆盖：
- BR-U04-40 audit 字段白名单 + 敏感值脱敏（quote_amount / cost_snapshot 仅记 `*_changed: true`）
- compute_promotion_changes：dict diff 正确性
- format_internal_code：业务键格式化（BR-U04-01）
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.modules.promotion.domain import (
    PROMOTION_SENSITIVE_FIELDS,
    PROMOTION_SENSITIVE_VALUE_FIELDS,
    build_promotion_audit_changes,
    compute_promotion_changes,
    compute_state_change,
    format_internal_code,
)
from app.modules.promotion.models import Promotion
from app.modules.promotion.schemas import PromotionUpdate


class TestPromotionAuditChanges:
    def test_sensitive_value_fields_redacted(self) -> None:
        changes = {
            "quote_amount": {"before": "100.00", "after": "200.00"},
            "cost_snapshot": {"before": "50.00", "after": "60.00"},
            "internal_code": {"before": "DE2605260001", "after": "DE2605260002"},
            "publish_url": {"before": None, "after": "https://x.com/note/1"},
            "publish_status": {"before": "未发布", "after": "已发布"},
            "remark": {"before": "old", "after": "new"},
        }
        audit = build_promotion_audit_changes(changes)

        # 敏感值：仅记 *_changed
        assert audit["quote_amount_changed"] is True
        assert audit["cost_snapshot_changed"] is True
        assert "quote_amount" not in audit
        assert "cost_snapshot" not in audit

        # 非敏感但白名单内：完整保留
        assert audit["internal_code"] == {
            "before": "DE2605260001",
            "after": "DE2605260002",
        }
        assert audit["publish_url"]["after"] == "https://x.com/note/1"
        assert audit["publish_status"]["after"] == "已发布"

        # 不在白名单：不写
        assert "remark" not in audit

    def test_constants(self) -> None:
        assert PROMOTION_SENSITIVE_FIELDS >= PROMOTION_SENSITIVE_VALUE_FIELDS
        assert "quote_amount" in PROMOTION_SENSITIVE_VALUE_FIELDS
        assert "cost_snapshot" in PROMOTION_SENSITIVE_VALUE_FIELDS
        assert "internal_code" in PROMOTION_SENSITIVE_FIELDS
        assert "internal_code" not in PROMOTION_SENSITIVE_VALUE_FIELDS

    def test_compute_state_change(self) -> None:
        result = compute_state_change(
            field="publish_status", before="未发布", after="已发布"
        )
        assert result == {"publish_status": {"before": "未发布", "after": "已发布"}}

    def test_compute_state_change_unchanged(self) -> None:
        assert compute_state_change(
            field="publish_status", before="已发布", after="已发布"
        ) == {}


class TestComputePromotionChanges:
    def _new_promotion(self, **kw: object) -> Promotion:
        defaults: dict[str, object] = {
            "tenant_id": uuid4(),
            "style_id": uuid4(),
            "blogger_id": uuid4(),
            "internal_code": "DE2605260001",
            "style_code_snapshot": "ST001",
            "style_short_name_snapshot": "测试款",
            "quote_amount": Decimal("500.00"),
            "platform": "小红书",
            "cooperation_date": date(2026, 5, 26),
            "publish_status": "未发布",
            "recall_status": "未召回",
            "settlement_status": "未核查",
            "is_active": True,
        }
        defaults.update(kw)
        return Promotion(**defaults)  # type: ignore[arg-type]

    def test_unchanged_returns_empty(self) -> None:
        p = self._new_promotion(remark="保持")
        payload = PromotionUpdate(remark="保持")
        assert compute_promotion_changes(p, payload) == {}

    def test_quote_amount_change_detected(self) -> None:
        p = self._new_promotion(quote_amount=Decimal("100.00"))
        payload = PromotionUpdate(quote_amount=Decimal("200.00"))
        changes = compute_promotion_changes(p, payload)
        assert "quote_amount" in changes
        assert changes["quote_amount"]["before"] == "100.00"
        assert changes["quote_amount"]["after"] == "200.00"

    def test_only_set_fields_in_diff(self) -> None:
        p = self._new_promotion(remark="原")
        payload = PromotionUpdate(note_title="新标题")
        changes = compute_promotion_changes(p, payload)
        assert set(changes.keys()) == {"note_title"}


class TestFormatInternalCode:
    def test_basic(self) -> None:
        result = format_internal_code(
            tenant_code="default",
            cooperation_date=date(2026, 5, 26),
            sequence=1,
        )
        assert result == "DE2605260001"

    def test_short_tenant_code_padded(self) -> None:
        result = format_internal_code(
            tenant_code="A", cooperation_date=date(2026, 1, 1), sequence=42
        )
        assert result.startswith("AX")  # 不足 2 字符补 X
        assert result == "AX2601010042"

    def test_empty_tenant_code(self) -> None:
        result = format_internal_code(
            tenant_code="", cooperation_date=date(2026, 1, 1), sequence=1
        )
        assert result == "XX2601010001"

    def test_sequence_padding(self) -> None:
        assert format_internal_code(
            tenant_code="DEMO",
            cooperation_date=date(2026, 5, 26),
            sequence=9999,
        ) == "DE2605269999"

    def test_uppercase(self) -> None:
        result = format_internal_code(
            tenant_code="abc",
            cooperation_date=date(2026, 5, 26),
            sequence=1,
        )
        assert result == "AB2605260001"
