"""U04 promotion 模块状态机定义（3 个并行）。

按 functional-design/business-rules.md §3 规则定义 transition 表，
基于 ``app.core.state_machine.StateMachine`` 通用基类。

3 个状态机：
1. ``PublishStatusMachine`` — publish_status (5 状态)
2. ``RecallStatusMachine`` — recall_status (4 状态)
3. ``SettlementStatusMachine`` — settlement_status (5 状态)

业务前置校验通过 ``assert_can_transition()`` classmethod 触发友好错误；
service 层并发安全通过 ``UPDATE WHERE old_state RETURNING`` 实施
（FB7 + 详见 nfr-design-patterns.md §2.2）。

跨状态机校验（BR-U04-24）：
- start_recall 前：publish_status ∈ {已发布, 已取消}
- approve 前：publish_status="已发布"
由 service 层显式实施，不在状态机内部耦合。
"""

from __future__ import annotations

from typing import ClassVar

from app.core.exceptions import IllegalStateTransitionError
from app.core.state_machine import TransitionRule

from app.modules.promotion.enums import (
    PublishStatus,
    RecallStatus,
    SettlementStatus,
)


# 角色常量（与 legacy_field_permissions 一致；U09 后切到 Permission 体系）
_ROLE_PR = "pr"
_ROLE_PR_MANAGER = "pr_manager"
_ROLE_ADMIN = "admin"
_ROLE_FINANCE = "finance"
_ROLE_SYSTEM = "system"


# ---------------------------------------------------------------------------
# PublishStatusMachine
# ---------------------------------------------------------------------------


class PublishStatusMachine:
    """publish_status 状态机（BR-U04-20）。"""

    transitions: ClassVar[tuple[TransitionRule, ...]] = (
        # 未发布 → 已发布
        TransitionRule(
            from_state=PublishStatus.UNPUBLISHED.value,
            action="publish",
            to_state=PublishStatus.PUBLISHED.value,
            actor_roles=(_ROLE_PR, _ROLE_PR_MANAGER, _ROLE_ADMIN),
            required_fields=("publish_url", "actual_publish_date"),
            side_effects=("advance_settlement_to_pending_review", "emit_promotion_published"),
        ),
        # 未发布 → 已取消
        TransitionRule(
            from_state=PublishStatus.UNPUBLISHED.value,
            action="cancel",
            to_state=PublishStatus.CANCELLED.value,
            actor_roles=(_ROLE_PR, _ROLE_PR_MANAGER, _ROLE_ADMIN),
            required_fields=("cancel_reason",),
        ),
        # 未发布 → 异常
        TransitionRule(
            from_state=PublishStatus.UNPUBLISHED.value,
            action="mark_abnormal",
            to_state=PublishStatus.ABNORMAL.value,
            actor_roles=(_ROLE_ADMIN, _ROLE_PR_MANAGER),
            required_fields=("remark",),
        ),
        # 已发布 → 异常
        TransitionRule(
            from_state=PublishStatus.PUBLISHED.value,
            action="mark_abnormal",
            to_state=PublishStatus.ABNORMAL.value,
            actor_roles=(_ROLE_ADMIN, _ROLE_PR_MANAGER),
        ),
        # 异常 → 未发布
        TransitionRule(
            from_state=PublishStatus.ABNORMAL.value,
            action="restore",
            to_state=PublishStatus.UNPUBLISHED.value,
            actor_roles=(_ROLE_ADMIN,),
        ),
        # 删除：当前状态 → 已删除（软删通用动作）
        TransitionRule(
            from_state=PublishStatus.UNPUBLISHED.value,
            action="delete",
            to_state=PublishStatus.DELETED.value,
            actor_roles=(_ROLE_ADMIN, _ROLE_PR_MANAGER),
        ),
        TransitionRule(
            from_state=PublishStatus.CANCELLED.value,
            action="delete",
            to_state=PublishStatus.DELETED.value,
            actor_roles=(_ROLE_ADMIN, _ROLE_PR_MANAGER),
        ),
    )

    @classmethod
    def assert_can_transition(
        cls,
        from_state: str | PublishStatus,
        to_state: str | PublishStatus,
        action: str,
    ) -> None:
        """业务前置校验：抛 IllegalStateTransitionError 含 from/to/action."""
        from_v = from_state.value if isinstance(from_state, PublishStatus) else from_state
        to_v = to_state.value if isinstance(to_state, PublishStatus) else to_state
        for t in cls.transitions:
            if t.from_state == from_v and t.to_state == to_v and t.action == action:
                return
        raise IllegalStateTransitionError(
            f"PublishStatusMachine: 不允许从 {from_v} 通过 {action} 转移到 {to_v}",
            details={
                "machine": "publish_status",
                "from_state": from_v,
                "to_state": to_v,
                "action": action,
            },
        )

    @classmethod
    def get_allowed_transitions(
        cls, from_state: str | PublishStatus
    ) -> list[tuple[str, str]]:
        """返回从某状态可达的所有 (action, to_state)，供前端展示按钮。"""
        from_v = from_state.value if isinstance(from_state, PublishStatus) else from_state
        return [(t.action, t.to_state) for t in cls.transitions if t.from_state == from_v]


# ---------------------------------------------------------------------------
# RecallStatusMachine
# ---------------------------------------------------------------------------


class RecallStatusMachine:
    """recall_status 状态机（BR-U04-21）。

    跨状态机前置（publish_status ∈ {已发布, 已取消}）由 service 层校验。
    """

    transitions: ClassVar[tuple[TransitionRule, ...]] = (
        TransitionRule(
            from_state=RecallStatus.NOT_RECALLED.value,
            action="start_recall",
            to_state=RecallStatus.RECALLING.value,
            actor_roles=(_ROLE_PR, _ROLE_PR_MANAGER, _ROLE_ADMIN),
        ),
        TransitionRule(
            from_state=RecallStatus.RECALLING.value,
            action="recall_success",
            to_state=RecallStatus.RECALLED_SUCCESS.value,
            actor_roles=(_ROLE_PR, _ROLE_PR_MANAGER, _ROLE_ADMIN),
        ),
        TransitionRule(
            from_state=RecallStatus.RECALLING.value,
            action="recall_failure",
            to_state=RecallStatus.RECALLED_FAILURE.value,
            actor_roles=(_ROLE_PR, _ROLE_PR_MANAGER, _ROLE_ADMIN),
        ),
        # 召回失败可重新发起
        TransitionRule(
            from_state=RecallStatus.RECALLED_FAILURE.value,
            action="start_recall",
            to_state=RecallStatus.RECALLING.value,
            actor_roles=(_ROLE_PR, _ROLE_PR_MANAGER, _ROLE_ADMIN),
        ),
    )

    @classmethod
    def assert_can_transition(
        cls,
        from_state: str | RecallStatus,
        to_state: str | RecallStatus,
        action: str,
    ) -> None:
        from_v = from_state.value if isinstance(from_state, RecallStatus) else from_state
        to_v = to_state.value if isinstance(to_state, RecallStatus) else to_state
        for t in cls.transitions:
            if t.from_state == from_v and t.to_state == to_v and t.action == action:
                return
        raise IllegalStateTransitionError(
            f"RecallStatusMachine: 不允许从 {from_v} 通过 {action} 转移到 {to_v}",
            details={
                "machine": "recall_status",
                "from_state": from_v,
                "to_state": to_v,
                "action": action,
            },
        )

    @classmethod
    def get_allowed_transitions(
        cls, from_state: str | RecallStatus
    ) -> list[tuple[str, str]]:
        from_v = from_state.value if isinstance(from_state, RecallStatus) else from_state
        return [(t.action, t.to_state) for t in cls.transitions if t.from_state == from_v]


# ---------------------------------------------------------------------------
# SettlementStatusMachine
# ---------------------------------------------------------------------------


class SettlementStatusMachine:
    """settlement_status 状态机（BR-U04-22）。

    跨状态机前置（approve 时 publish_status="已发布" + 不能自审）由 service 层校验。
    auto_advance 由 publish action 同事务驱动（不由前端触发）。
    """

    transitions: ClassVar[tuple[TransitionRule, ...]] = (
        # 系统自动推进：未核查 → 待核查
        TransitionRule(
            from_state=SettlementStatus.NOT_REVIEWED.value,
            action="auto_advance",
            to_state=SettlementStatus.PENDING_REVIEW.value,
            actor_roles=(_ROLE_SYSTEM,),
        ),
        # 待核查 → 待付款
        TransitionRule(
            from_state=SettlementStatus.PENDING_REVIEW.value,
            action="approve",
            to_state=SettlementStatus.PENDING_PAYMENT.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN),
            side_effects=("emit_settlement_requested",),
        ),
        # 待核查 → 已驳回
        TransitionRule(
            from_state=SettlementStatus.PENDING_REVIEW.value,
            action="reject",
            to_state=SettlementStatus.REJECTED.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN),
            required_fields=("review_reason",),
        ),
        # 已驳回 → 待核查（PR 修改后重新提交，service 推进）
        TransitionRule(
            from_state=SettlementStatus.REJECTED.value,
            action="resubmit",
            to_state=SettlementStatus.PENDING_REVIEW.value,
            actor_roles=(_ROLE_PR, _ROLE_SYSTEM),
        ),
        # 待付款 → 已付款（U05 反向通知）
        TransitionRule(
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            action="mark_paid",
            to_state=SettlementStatus.PAID.value,
            actor_roles=(_ROLE_FINANCE, _ROLE_ADMIN, _ROLE_SYSTEM),
        ),
    )

    @classmethod
    def assert_can_transition(
        cls,
        from_state: str | SettlementStatus,
        to_state: str | SettlementStatus,
        action: str,
    ) -> None:
        from_v = from_state.value if isinstance(from_state, SettlementStatus) else from_state
        to_v = to_state.value if isinstance(to_state, SettlementStatus) else to_state
        for t in cls.transitions:
            if t.from_state == from_v and t.to_state == to_v and t.action == action:
                return
        raise IllegalStateTransitionError(
            f"SettlementStatusMachine: 不允许从 {from_v} 通过 {action} 转移到 {to_v}",
            details={
                "machine": "settlement_status",
                "from_state": from_v,
                "to_state": to_v,
                "action": action,
            },
        )

    @classmethod
    def get_allowed_transitions(
        cls, from_state: str | SettlementStatus
    ) -> list[tuple[str, str]]:
        from_v = from_state.value if isinstance(from_state, SettlementStatus) else from_state
        return [(t.action, t.to_state) for t in cls.transitions if t.from_state == from_v]


__all__ = [
    "PublishStatusMachine",
    "RecallStatusMachine",
    "SettlementStatusMachine",
]
