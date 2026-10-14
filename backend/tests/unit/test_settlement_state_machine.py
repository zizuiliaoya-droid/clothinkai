"""U05 单元测试：SettlementStatusMachine（流程线 PR-6 F0 ~ F6：去掉核查，入口改待付款）。"""

from __future__ import annotations

import pytest

from app.core.exceptions import IllegalStateTransitionError
from app.modules.finance.enums import SettlementStatus
from app.modules.finance.state_machines import SettlementStatusMachine


@pytest.mark.unit
class TestSettlementStateMachineAllowed:
    """6 个合法转移逐一验证（设计 3.4 结款单状态机）。"""

    @pytest.mark.parametrize(
        ("from_state", "action", "to_state"),
        [
            ("待核查", "approve", "待付款"),  # F0：只给上线前的旧单
            ("待付款", "reject", "已驳回"),  # F5
            ("待财务付款", "reject", "已驳回"),  # F5：填完实付、付款前发现收款码不对
            ("待付款", "fill_payment", "待财务付款"),  # F3
            ("待财务付款", "mark_paid", "已付款"),  # F4
            ("已驳回", "resubmit", "待付款"),  # F6：重提回到待付款
        ],
    )
    def test_allowed_transition(self, from_state: str, action: str, to_state: str) -> None:
        # 不抛异常即通过
        SettlementStatusMachine.assert_can_transition(
            from_state=from_state, to_state=to_state, action=action
        )

    def test_accepts_enum_inputs(self) -> None:
        SettlementStatusMachine.assert_can_transition(
            from_state=SettlementStatus.PENDING_REVIEW,
            to_state=SettlementStatus.PENDING_PAYMENT,
            action="approve",
        )


@pytest.mark.unit
class TestSettlementStateMachineRejected:
    """非法转移必须抛 IllegalStateTransitionError。"""

    @pytest.mark.parametrize(
        ("from_state", "action", "to_state"),
        [
            # 新单不再经过核查：待付款没有 approve
            ("待付款", "approve", "待付款"),
            # 旧的待核查单只能核查通过（F0），不再从这里驳回
            ("待核查", "reject", "已驳回"),
            # 待核查不能直接到待财务付款
            ("待核查", "fill_payment", "待财务付款"),
            # 已付款是终态，不能再推进
            ("已付款", "reject", "已驳回"),
            ("已付款", "resubmit", "待付款"),
            # 重提不再回到待核查
            ("已驳回", "resubmit", "待核查"),
            # 待核查不能直接 mark_paid
            ("待核查", "mark_paid", "已付款"),
            # action 与 to_state 不匹配
            ("待核查", "approve", "已驳回"),
        ],
    )
    def test_rejected_transition(self, from_state: str, action: str, to_state: str) -> None:
        with pytest.raises(IllegalStateTransitionError) as exc_info:
            SettlementStatusMachine.assert_can_transition(
                from_state=from_state, to_state=to_state, action=action
            )
        # 异常 details 含 from/to/action 便于排查
        details = exc_info.value.details
        assert details["from_state"] == from_state
        assert details["to_state"] == to_state
        assert details["action"] == action


@pytest.mark.unit
class TestGetAllowedTransitions:
    def test_pending_review_only_legacy_approve(self) -> None:
        assert SettlementStatusMachine.get_allowed_transitions("待核查") == [("approve", "待付款")]

    def test_paid_is_terminal(self) -> None:
        assert SettlementStatusMachine.get_allowed_transitions("已付款") == []

    def test_pending_payment_has_two_paths(self) -> None:
        allowed = SettlementStatusMachine.get_allowed_transitions("待付款")
        actions = {a for a, _ in allowed}
        assert actions == {"reject", "fill_payment"}

    def test_pending_finance_can_pay_or_reject(self) -> None:
        allowed = set(SettlementStatusMachine.get_allowed_transitions("待财务付款"))
        assert allowed == {("mark_paid", "已付款"), ("reject", "已驳回")}

    def test_rejected_resubmits_to_pending_payment(self) -> None:
        assert SettlementStatusMachine.get_allowed_transitions("已驳回") == [("resubmit", "待付款")]
