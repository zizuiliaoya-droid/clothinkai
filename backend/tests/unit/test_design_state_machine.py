"""U10a 单元测试：DesignStateMachine 转移表 + available_actions + 回退映射。"""

from __future__ import annotations

import pytest

from app.modules.design.domain import can_reject, compute_available_actions
from app.modules.design.enums import REJECT_PREVIOUS
from app.modules.design.enums import DesignStatus as DS
from app.modules.design.state_machines import (
    DESIGN_TRANSITIONS,
    make_design_state_machine,
)


class _Stub:
    def __init__(self, status: str) -> None:
        self.design_status = status


@pytest.mark.unit
class TestTransitions:
    @pytest.mark.parametrize(
        "from_status,action,to_status",
        [
            (DS.DESIGNING.value, "submit_fabric", DS.PATTERNING.value),
            (DS.PATTERNING.value, "submit_grading", DS.CRAFTING.value),
            (DS.CRAFTING.value, "submit_craft", DS.COMPLETING.value),
            (DS.COMPLETING.value, "submit_costing", DS.PRICING.value),
            (DS.PRICING.value, "confirm_price", DS.MASS_PRODUCTION.value),
        ],
    )
    def test_legal_transitions(self, from_status, action, to_status) -> None:
        sm = make_design_state_machine(_Stub(from_status))
        rule = sm._find_rule(action)
        assert rule is not None
        assert rule.to_state == to_status

    def test_illegal_transition_returns_none(self) -> None:
        sm = make_design_state_machine(_Stub(DS.DESIGNING.value))
        assert sm._find_rule("confirm_price") is None

    def test_terminal_no_actions(self) -> None:
        sm = make_design_state_machine(_Stub(DS.MASS_PRODUCTION.value))
        assert sm.get_valid_actions() == []

    def test_actor_roles_declared(self) -> None:
        for rule in DESIGN_TRANSITIONS:
            assert rule.actor_roles  # 每条转移都声明了允许角色


@pytest.mark.unit
class TestAvailableActions:
    def test_designer_in_designing(self) -> None:
        actions = compute_available_actions(DS.DESIGNING.value, {"designer"})
        assert "submit_fabric" in actions

    def test_pattern_maker_in_patterning(self) -> None:
        actions = compute_available_actions(DS.PATTERNING.value, {"pattern_maker"})
        assert "submit_grading" in actions
        assert "reject" in actions

    def test_admin_can_cancel_nonterminal(self) -> None:
        actions = compute_available_actions(DS.CRAFTING.value, {"admin"})
        assert "cancel" in actions

    def test_admin_no_cancel_terminal(self) -> None:
        actions = compute_available_actions(DS.MASS_PRODUCTION.value, {"admin"})
        assert "cancel" not in actions

    def test_wrong_role_no_actions(self) -> None:
        actions = compute_available_actions(DS.DESIGNING.value, {"finance"})
        assert actions == []


@pytest.mark.unit
class TestRejectMap:
    @pytest.mark.parametrize(
        "cur,prev",
        [
            (DS.PATTERNING.value, DS.DESIGNING.value),
            (DS.CRAFTING.value, DS.PATTERNING.value),
            (DS.COMPLETING.value, DS.CRAFTING.value),
            (DS.PRICING.value, DS.COMPLETING.value),
        ],
    )
    def test_reject_previous(self, cur, prev) -> None:
        assert REJECT_PREVIOUS[cur] == prev
        assert can_reject(cur) is True

    def test_designing_cannot_reject(self) -> None:
        assert can_reject(DS.DESIGNING.value) is False

    def test_terminal_cannot_reject(self) -> None:
        assert can_reject(DS.MASS_PRODUCTION.value) is False
