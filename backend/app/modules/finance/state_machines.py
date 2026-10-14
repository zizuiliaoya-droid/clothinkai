"""U05 finance 模块状态机定义（1 个）。

按 functional-design/business-rules.md §4 + nfr-design-patterns.md tech-stack §2 实施。

复用 U04 状态机模式：
- ``app.core.state_machine.TransitionRule`` 通用基类
- classmethod ``assert_can_transition`` / ``get_allowed_transitions``
- service 层并发安全通过 ``UPDATE WHERE old_state RETURNING`` 实施（FB7）

5 状态 6 转移（流程线 PR-6，设计 3.4：去掉核查，F1 建单直接落在「待付款」）：
- 待核查 → 待付款（approve，F0：只给上线前已建、停在待核查的旧单）
- 待付款 → 已驳回（reject，F5）
- 待财务付款 → 已驳回（reject，F5：填完实付、付款前发现收款码不对）
- 待付款 → 待财务付款（fill_payment，F3）
- 待财务付款 → 已付款（mark_paid，F4）
- 已驳回 → 待付款（resubmit，F6：由推广单 R7 发起）

跨实体校验（自审禁止）：
- approve 时 reviewer.id != settlement.pr_id（来自 promotion.pr_id 冗余）
- 由 service 层显式校验，不在状态机内部耦合
"""

from __future__ import annotations

from typing import ClassVar

from app.core.exceptions import IllegalStateTransitionError
from app.core.state_machine import TransitionRule
from app.modules.finance.enums import SettlementStatus

# 角色常量（与 legacy_field_permissions 一致；U09 后切到 Permission 体系）
_ROLE_PR_MANAGER = "pr_manager"
_ROLE_FINANCE = "finance"
_ROLE_ADMIN = "admin"
_ROLE_PR = "pr"
_ROLE_SYSTEM = "system"


class SettlementStatusMachine:
    """settlement_status 状态机（5 状态 6 转移）。"""

    transitions: ClassVar[tuple[TransitionRule, ...]] = (
        # F0 待核查 → 待付款（approve）：只给上线前停在待核查的旧单，新单不再进待核查
        TransitionRule(
            from_state=SettlementStatus.PENDING_REVIEW.value,
            action="approve",
            to_state=SettlementStatus.PENDING_PAYMENT.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN),
        ),
        # F5 待付款 → 已驳回（reject）
        TransitionRule(
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            action="reject",
            to_state=SettlementStatus.REJECTED.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_FINANCE, _ROLE_ADMIN),
            required_fields=("review_reason",),
        ),
        # F5 待财务付款 → 已驳回（reject）：填完实付、付款前发现收款码不对要能退回
        TransitionRule(
            from_state=SettlementStatus.PENDING_FINANCE.value,
            action="reject",
            to_state=SettlementStatus.REJECTED.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_FINANCE, _ROLE_ADMIN),
            required_fields=("review_reason",),
        ),
        # 待付款 → 待财务付款（fill_payment）
        TransitionRule(
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            action="fill_payment",
            to_state=SettlementStatus.PENDING_FINANCE.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN),
            required_fields=("payment_amount",),
        ),
        # 待财务付款 → 已付款（mark_paid）
        TransitionRule(
            from_state=SettlementStatus.PENDING_FINANCE.value,
            action="mark_paid",
            to_state=SettlementStatus.PAID.value,
            actor_roles=(_ROLE_FINANCE, _ROLE_ADMIN),
            required_fields=("payment_date", "payment_proof_attachment_id"),
        ),
        # F6 已驳回 → 待付款（resubmit）：推广单 R7 重提结款时由事件监听推进
        TransitionRule(
            from_state=SettlementStatus.REJECTED.value,
            action="resubmit",
            to_state=SettlementStatus.PENDING_PAYMENT.value,
            actor_roles=(_ROLE_PR_MANAGER, _ROLE_ADMIN, _ROLE_SYSTEM),
        ),
    )

    @classmethod
    def assert_can_transition(
        cls,
        from_state: str | SettlementStatus,
        to_state: str | SettlementStatus,
        action: str,
    ) -> None:
        """业务前置校验：抛 IllegalStateTransitionError 含 from/to/action."""
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
    def get_allowed_transitions(cls, from_state: str | SettlementStatus) -> list[tuple[str, str]]:
        """返回从某状态可达的所有 (action, to_state)，供前端展示按钮。"""
        from_v = from_state.value if isinstance(from_state, SettlementStatus) else from_state
        return [(t.action, t.to_state) for t in cls.transitions if t.from_state == from_v]


__all__ = ["SettlementStatusMachine"]
