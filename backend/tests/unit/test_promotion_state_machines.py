"""U04 状态机单元测试（3 个状态机）。

覆盖：
- 全部合法转移
- 非法转移抛 IllegalStateTransitionError
- get_allowed_transitions 返回正确清单
"""

from __future__ import annotations

import pytest

from app.core.exceptions import IllegalStateTransitionError
from app.modules.promotion.enums import (
    PublishStatus,
    RecallStatus,
    SettlementStatus,
)
from app.modules.promotion.state_machines import (
    PublishStatusMachine,
    RecallStatusMachine,
    SettlementStatusMachine,
)


class TestPublishStatusMachine:
    @pytest.mark.parametrize(
        "from_state, to_state, action",
        [
            (PublishStatus.UNPUBLISHED, PublishStatus.PUBLISHED, "publish"),
            (PublishStatus.UNPUBLISHED, PublishStatus.CANCELLED, "cancel"),
            (PublishStatus.UNPUBLISHED, PublishStatus.ABNORMAL, "mark_abnormal"),
            (PublishStatus.PUBLISHED, PublishStatus.ABNORMAL, "mark_abnormal"),
            (PublishStatus.ABNORMAL, PublishStatus.UNPUBLISHED, "restore"),
        ],
    )
    def test_legal_transitions(
        self, from_state: PublishStatus, to_state: PublishStatus, action: str
    ) -> None:
        # 合法 → 不抛错
        PublishStatusMachine.assert_can_transition(from_state, to_state, action)

    def test_published_cannot_cancel(self) -> None:
        with pytest.raises(IllegalStateTransitionError) as exc:
            PublishStatusMachine.assert_can_transition(
                PublishStatus.PUBLISHED, PublishStatus.CANCELLED, "cancel"
            )
        assert exc.value.details["machine"] == "publish_status"

    def test_published_cannot_publish_again(self) -> None:
        with pytest.raises(IllegalStateTransitionError):
            PublishStatusMachine.assert_can_transition(
                PublishStatus.PUBLISHED, PublishStatus.PUBLISHED, "publish"
            )

    def test_get_allowed_from_unpublished(self) -> None:
        actions = PublishStatusMachine.get_allowed_transitions(
            PublishStatus.UNPUBLISHED
        )
        action_names = {a for a, _ in actions}
        assert {"publish", "cancel", "mark_abnormal", "delete"} <= action_names


class TestRecallStatusMachine:
    @pytest.mark.parametrize(
        "from_state, to_state, action",
        [
            (RecallStatus.NOT_RECALLED, RecallStatus.RECALLING, "start_recall"),
            (RecallStatus.RECALLING, RecallStatus.RECALLED_SUCCESS, "recall_success"),
            (RecallStatus.RECALLING, RecallStatus.RECALLED_FAILURE, "recall_failure"),
            (RecallStatus.RECALLED_FAILURE, RecallStatus.RECALLING, "start_recall"),
        ],
    )
    def test_legal_transitions(
        self, from_state: RecallStatus, to_state: RecallStatus, action: str
    ) -> None:
        RecallStatusMachine.assert_can_transition(from_state, to_state, action)

    def test_success_is_terminal(self) -> None:
        """召回成功后无后续转移."""
        actions = RecallStatusMachine.get_allowed_transitions(
            RecallStatus.RECALLED_SUCCESS
        )
        assert actions == []

    def test_not_recalled_cannot_skip_to_success(self) -> None:
        with pytest.raises(IllegalStateTransitionError):
            RecallStatusMachine.assert_can_transition(
                RecallStatus.NOT_RECALLED,
                RecallStatus.RECALLED_SUCCESS,
                "recall_success",
            )


class TestSettlementStatusMachine:
    @pytest.mark.parametrize(
        "from_state, to_state, action",
        [
            (SettlementStatus.NOT_REVIEWED, SettlementStatus.PENDING_REVIEW, "auto_advance"),
            (SettlementStatus.PENDING_REVIEW, SettlementStatus.PENDING_PAYMENT, "approve"),
            (SettlementStatus.PENDING_REVIEW, SettlementStatus.REJECTED, "reject"),
            (SettlementStatus.REJECTED, SettlementStatus.PENDING_REVIEW, "resubmit"),
            (SettlementStatus.PENDING_PAYMENT, SettlementStatus.PAID, "mark_paid"),
        ],
    )
    def test_legal_transitions(
        self,
        from_state: SettlementStatus,
        to_state: SettlementStatus,
        action: str,
    ) -> None:
        SettlementStatusMachine.assert_can_transition(from_state, to_state, action)

    def test_paid_is_terminal(self) -> None:
        actions = SettlementStatusMachine.get_allowed_transitions(
            SettlementStatus.PAID
        )
        assert actions == []

    def test_not_reviewed_cannot_directly_approve(self) -> None:
        with pytest.raises(IllegalStateTransitionError):
            SettlementStatusMachine.assert_can_transition(
                SettlementStatus.NOT_REVIEWED,
                SettlementStatus.PENDING_PAYMENT,
                "approve",
            )

    def test_string_arg_works_too(self) -> None:
        """assert_can_transition 同时接受 Enum 和 str."""
        SettlementStatusMachine.assert_can_transition(
            "未核查", "待核查", "auto_advance"
        )
