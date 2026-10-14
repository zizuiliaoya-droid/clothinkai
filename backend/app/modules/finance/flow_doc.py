"""结款单的矩阵快照（流程线 5.4）与状态机（``allowed_actions``，计划 D10）。

快照只放矩阵要读的东西；纯 Python、不碰库。``allowed_actions`` 不另写一张转移表，直接由 service 用的
``SettlementStatusMachine`` 推导，两边不会漂（单测守）。import 本模块会顺带 import ``finance.flow_matrix``，
把结款单矩阵登记进 ``MATRICES``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.modules.finance import flow_matrix as _flow_matrix  # noqa: F401  登记 MATRICES
from app.modules.finance.enums import SettlementStatus
from app.modules.finance.state_machines import SettlementStatusMachine
from app.modules.flow.matrix import FlowDocBase

# 状态机的转移动作 → 矩阵动作键。重提（resubmit，F6）没有结款页按钮：由推广单 R7 发事件推进
TRANSITION_ACTIONS: dict[str, str] = {
    "approve": "legacy_approve",
    "reject": "reject",
    "fill_payment": "fill_payment",
    "mark_paid": "upload_proof",
}
# 追加项不改状态、不进状态机：service 只在待付款允许（BR-U05-40）
_EXTRA_ITEM_STATES = frozenset({SettlementStatus.PENDING_PAYMENT.value})


@dataclass(frozen=True, kw_only=True)
class SettlementDoc(FlowDocBase):
    """结款单快照。``stage`` = ``state`` = 结款状态存储值；没有 ``column``。

    ``owner_id`` = ``negotiator_id`` = 结款单 ``pr_id``（自审规则 ``NotSubmitter`` 读 ``owner_id``）。
    """

    kind: ClassVar[str] = "settlement"

    def allowed_actions(self) -> frozenset[str]:
        allowed = {
            TRANSITION_ACTIONS[action]
            for action, _to in SettlementStatusMachine.get_allowed_transitions(self.state)
            if action in TRANSITION_ACTIONS
        }
        if self.state in _EXTRA_ITEM_STATES:
            allowed.add("extra_item")
        return frozenset(allowed)


def build_settlement_doc(*, status: str, pr_id: UUID | None) -> SettlementDoc:
    return SettlementDoc(stage=status, state=status, owner_id=pr_id, negotiator_id=pr_id)


__all__ = ["TRANSITION_ACTIONS", "SettlementDoc", "build_settlement_doc"]
