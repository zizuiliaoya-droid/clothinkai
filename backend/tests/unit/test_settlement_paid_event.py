"""U05 单元测试：SettlementPaid 事件（required_handler=False，FB5）。

验证与 SettlementRequested（required_handler=True 强一致）的不对称语义；
流程线 PR-6 起结款链另有两个强一致事件（SettlementRejected / SettlementResubmitted，R6 / R7），
付款（R8）仍是通知类：监听只把推广单 待付款 → 已付款，覆盖不了结款驳回 / 已结款
（端到端见 test_e2e_review_to_paid 末段的重放）。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.finance.events import SettlementPaid, SettlementRejected
from app.modules.promotion.events import SettlementRequested, SettlementResubmitted


@pytest.mark.unit
class TestSettlementPaidEvent:
    def test_required_handler_is_false(self) -> None:
        """FB5：SettlementPaid 是通知类，缺 handler 不抛错。"""
        assert SettlementPaid.required_handler is False

    def test_event_type_name(self) -> None:
        assert SettlementPaid.event_type == "SettlementPaid"

    def test_is_frozen(self) -> None:
        event = _make_paid_event()
        with pytest.raises(FrozenInstanceError):
            event.payment_amount = Decimal("999")  # type: ignore[misc]

    def test_carries_required_fields(self) -> None:
        event = _make_paid_event()
        assert event.settlement_id is not None
        assert event.promotion_id is not None
        assert event.payment_date == date(2026, 5, 26)
        assert event.payment_amount == Decimal("500.00")


@pytest.mark.unit
class TestAsymmetryWithSettlementRequested:
    """FB1 vs FB5：正向强一致 vs 反向通知类的不对称。"""

    def test_settlement_requested_is_required(self) -> None:
        assert SettlementRequested.required_handler is True

    def test_settlement_paid_is_not_required(self) -> None:
        assert SettlementPaid.required_handler is False

    def test_asymmetry(self) -> None:
        # 正向必须有 handler（U05 未部署 → U04 review approve 失败）
        # 反向可丢（U04 listener 缺失不阻塞 U05 mark_paid）
        assert SettlementRequested.required_handler != SettlementPaid.required_handler


@pytest.mark.unit
class TestAsymmetryWithSettlementFlowEvents:
    """流程线 PR-6：驳回 / 重提必须有监听（两张表同事务，D7），付款仍可丢。"""

    def test_reject_and_resubmit_are_required(self) -> None:
        assert SettlementRejected.required_handler is True
        assert SettlementResubmitted.required_handler is True

    def test_paid_stays_notification(self) -> None:
        # 付款回写缺监听不阻塞财务付款；驳回 / 重提缺监听必须报错
        assert SettlementPaid.required_handler != SettlementRejected.required_handler


def _make_paid_event() -> SettlementPaid:
    return SettlementPaid(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        tenant_id=uuid4(),
        settlement_id=uuid4(),
        promotion_id=uuid4(),
        payment_amount=Decimal("500.00"),
        payment_date=date(2026, 5, 26),
        paid_by=uuid4(),
    )
