"""U05 单元测试：SettlementPaid 事件（required_handler=False，FB5）。

验证与 SettlementRequested（required_handler=True 强一致）的不对称语义。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from app.modules.finance.events import SettlementPaid
from app.modules.promotion.events import SettlementRequested


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
        assert (
            SettlementRequested.required_handler
            != SettlementPaid.required_handler
        )


def _make_paid_event() -> SettlementPaid:
    return SettlementPaid(
        event_id=uuid4(),
        timestamp=datetime.now(timezone.utc),
        tenant_id=uuid4(),
        settlement_id=uuid4(),
        promotion_id=uuid4(),
        payment_amount=Decimal("500.00"),
        payment_date=date(2026, 5, 26),
        paid_by=uuid4(),
    )
