"""U05 单元测试：domain 层（format_settlement_no + audit 脱敏 + dict diff）。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.finance.domain import (
    build_settlement_audit_changes,
    compute_state_change,
    format_settlement_no,
)


@pytest.mark.unit
class TestFormatSettlementNo:
    def test_basic_format(self) -> None:
        no = format_settlement_no(
            tenant_code="demo", date_key=date(2026, 5, 26), sequence=1
        )
        # <prefix 2 大写>S<yyMMdd><0001>
        assert no == "DES2605260001"

    def test_prefix_padding_when_short(self) -> None:
        no = format_settlement_no(
            tenant_code="a", date_key=date(2026, 5, 26), sequence=42
        )
        assert no == "AXS2605260042"

    def test_prefix_padding_when_empty(self) -> None:
        no = format_settlement_no(
            tenant_code="", date_key=date(2026, 1, 1), sequence=9999
        )
        assert no == "XXS2601019999"

    def test_sequence_zero_padded_to_4(self) -> None:
        no = format_settlement_no(
            tenant_code="TT", date_key=date(2026, 12, 31), sequence=7
        )
        assert no.endswith("0007")
        assert no == "TTS2612310007"


@pytest.mark.unit
class TestBuildSettlementAuditChanges:
    """FB3 + FB4：金额脱敏 + attachment_id 脱敏。"""

    def test_amount_fields_masked_to_changed_flag(self) -> None:
        changes = {
            "amount": {"before": "100.00", "after": "200.00"},
            "total_amount": {"before": "100.00", "after": "250.00"},
            "payment_amount": {"before": None, "after": "200.00"},
        }
        safe = build_settlement_audit_changes(changes)
        # 仅记 *_changed: true，不含原始金额值
        assert safe == {
            "amount_changed": True,
            "total_amount_changed": True,
            "payment_amount_changed": True,
        }
        assert "100.00" not in str(safe)
        assert "200.00" not in str(safe)

    def test_attachment_id_masked(self) -> None:
        changes = {
            "payment_proof_attachment_id": {
                "before": None,
                "after": "11111111-1111-1111-1111-111111111111",
            }
        }
        safe = build_settlement_audit_changes(changes)
        assert safe == {"attachment_id_changed": True}
        assert "11111111" not in str(safe)

    def test_non_sensitive_fields_skipped(self) -> None:
        changes = {
            "remark": {"before": "a", "after": "b"},
            "note_title": {"before": None, "after": "标题"},
        }
        safe = build_settlement_audit_changes(changes)
        assert safe == {}

    def test_status_and_action_recorded_verbatim(self) -> None:
        changes = {
            "settlement_status": {"before": "待核查", "after": "待付款"},
            "review_action": {"before": None, "after": "approve"},
        }
        safe = build_settlement_audit_changes(changes)
        assert safe["settlement_status"] == {
            "before": "待核查",
            "after": "待付款",
        }
        assert safe["review_action"] == {"before": None, "after": "approve"}


@pytest.mark.unit
class TestComputeStateChange:
    def test_records_change(self) -> None:
        diff = compute_state_change(
            field="settlement_status", before="待核查", after="待付款"
        )
        assert diff == {
            "settlement_status": {"before": "待核查", "after": "待付款"}
        }

    def test_no_change_returns_empty(self) -> None:
        diff = compute_state_change(
            field="settlement_status", before="待付款", after="待付款"
        )
        assert diff == {}


@pytest.mark.unit
def test_decimal_serialization_roundtrip() -> None:
    """金额 diff 时 Decimal 应能被 _serialize 处理（间接通过 audit changes）。"""
    # build_settlement_audit_changes 对金额脱敏，不应抛序列化错误
    changes = {"amount": {"before": Decimal("1.5"), "after": Decimal("2.5")}}
    safe = build_settlement_audit_changes(changes)
    assert safe == {"amount_changed": True}
