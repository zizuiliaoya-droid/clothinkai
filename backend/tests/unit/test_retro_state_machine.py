"""复盘三态：发布即待复盘，主管写即完成，只有管理员可追加修订。"""

from __future__ import annotations

import pytest

from app.core.exceptions import IllegalStateTransitionError
from app.modules.promotion.enums import RetroStatus
from app.modules.promotion.state_machines import RetroStatusMachine, SettlementStatusMachine

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("source", "action", "target"),
    [
        ("未开始", "publish", "待复盘"),
        ("待复盘", "write_retro", "已完成"),
        ("已完成", "revise_retro", "已完成"),
    ],
)
def test_three_state_transitions(source: str, action: str, target: str) -> None:
    RetroStatusMachine.assert_can_transition(source, target, action)


@pytest.mark.parametrize(
    ("source", "action", "target"),
    [
        ("未开始", "record_metrics", "待复盘"),
        ("待复盘", "submit_retro", "待确认"),
        ("待确认", "confirm_retro", "已完成"),
        ("待确认", "reject_retro", "待复盘"),
        ("未开始", "write_retro", "已完成"),
        ("已完成", "write_retro", "已完成"),
    ],
)
def test_removed_or_invalid_transitions(source: str, action: str, target: str) -> None:
    with pytest.raises(IllegalStateTransitionError):
        RetroStatusMachine.assert_can_transition(source, target, action)


def test_three_values_and_orthogonal_settlement() -> None:
    assert {s.value for s in RetroStatus} == {"未开始", "待复盘", "已完成"}
    assert SettlementStatusMachine.get_allowed_transitions("已付款") == [("settle_chat", "已结款")]
    assert SettlementStatusMachine.get_allowed_transitions("已结款") == []
