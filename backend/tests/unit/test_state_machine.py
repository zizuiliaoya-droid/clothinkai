"""单元测试：StateMachine 基类（U01 提供，U03/U04/U05/U10a 复用）。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.core.exceptions import IllegalStateTransitionError
from app.core.state_machine import StateMachine, TransitionRule


@dataclass
class FakeEntity:
    status: str = "draft"


# 简单的转移表（模拟一个 draft → submitted → approved 流程）
TEST_TRANSITIONS = (
    TransitionRule(
        from_state="draft",
        action="submit",
        to_state="submitted",
        required_fields=("title",),
    ),
    TransitionRule(
        from_state="submitted",
        action="approve",
        to_state="approved",
        actor_roles=("manager",),
    ),
    TransitionRule(
        from_state="submitted",
        action="reject",
        to_state="draft",
    ),
)


@pytest.mark.unit
class TestStateMachine:
    def test_can_transition(self) -> None:
        e = FakeEntity()
        sm = StateMachine(target=e, transition_table=TEST_TRANSITIONS)
        assert sm.can_transition("submit") is True
        assert sm.can_transition("approve") is False  # 当前状态 draft 不允许

    def test_simple_transition(self) -> None:
        e = FakeEntity()
        sm = StateMachine(target=e, transition_table=TEST_TRANSITIONS)
        sm.transition("submit", payload={"title": "x"})
        assert e.status == "submitted"

    def test_illegal_transition(self) -> None:
        e = FakeEntity()
        sm = StateMachine(target=e, transition_table=TEST_TRANSITIONS)
        with pytest.raises(IllegalStateTransitionError):
            sm.transition("approve")
        assert e.status == "draft"  # 状态不变

    def test_required_fields_missing(self) -> None:
        e = FakeEntity()
        sm = StateMachine(target=e, transition_table=TEST_TRANSITIONS)
        with pytest.raises(IllegalStateTransitionError) as exc:
            sm.transition("submit", payload={})
        assert "title" in str(exc.value.details.get("missing_fields", []))
        assert e.status == "draft"

    def test_actor_role_required(self) -> None:
        e = FakeEntity(status="submitted")
        sm = StateMachine(target=e, transition_table=TEST_TRANSITIONS)
        # 没有 manager 角色：禁止
        with pytest.raises(IllegalStateTransitionError):
            sm.transition("approve", actor_roles=["pr"])

        # 有 manager：允许
        sm.transition("approve", actor_roles=["manager"])
        assert e.status == "approved"

    def test_actor_role_skipped_when_none(self) -> None:
        """actor_roles=None 时跳过角色校验（系统任务）。"""
        e = FakeEntity(status="submitted")
        sm = StateMachine(target=e, transition_table=TEST_TRANSITIONS)
        sm.transition("approve", actor_roles=None)
        assert e.status == "approved"

    def test_get_valid_actions(self) -> None:
        e = FakeEntity()
        sm = StateMachine(target=e, transition_table=TEST_TRANSITIONS)
        assert sm.get_valid_actions() == ["submit"]

        e.status = "submitted"
        actions = sm.get_valid_actions(actor_roles=["manager"])
        assert set(actions) == {"approve", "reject"}

    def test_get_valid_actions_filters_by_role(self) -> None:
        e = FakeEntity(status="submitted")
        sm = StateMachine(target=e, transition_table=TEST_TRANSITIONS)
        # pr 角色不能 approve，但能 reject（无 actor_roles 限制）
        actions = sm.get_valid_actions(actor_roles=["pr"])
        assert "approve" not in actions
        assert "reject" in actions
