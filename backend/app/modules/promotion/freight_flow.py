"""R33四态能力，复用scope/字段/谈款人规则；财务页隐藏主管审核动作。"""

from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID

from app.modules.flow.matrix import (
    MATRICES,
    Cell,
    FieldWrite,
    FlowDocBase,
    Matrix,
    NotPrOwner,
    Row,
    Scope,
)
from app.modules.promotion.freight_rules import TRANSITIONS

ACTION_MAP = {
    "approve": "freight_review",
    "reject": "freight_review",
    "pay": "freight_pay",
    "reject_payment": "freight_reject_payment",
    "resubmit": "freight_resubmit",
}


@dataclass(frozen=True, kw_only=True)
class ReturnFreightDoc(FlowDocBase):
    kind: ClassVar[str] = "freight"

    def allowed_actions(self) -> frozenset[str]:
        return frozenset(
            ACTION_MAP[action] for (status, action) in TRANSITIONS if status == self.state
        )


def freight_doc(status: str, negotiator_id: UUID | None) -> ReturnFreightDoc:
    return ReturnFreightDoc(stage=status, state=status, negotiator_id=negotiator_id)


FREIGHT_MATRIX = Matrix(
    kind="freight",
    stages=("待审核", "待付款", "已付款", "已驳回"),
    rows=(
        Row(
            "freight_review",
            "action",
            {
                "待审核": Cell(
                    edit=(Scope("promotion_review", "approve"), NotPrOwner()),
                    read=(Scope("promotion_review", "approve"),),
                )
            },
        ),
        Row("freight_pay", "action", {"待付款": Cell(edit=(Scope("finance.settlement", "pay"),))}),
        Row(
            "freight_reject_payment",
            "action",
            {"待付款": Cell(edit=(Scope("finance.settlement", "pay"),))},
        ),
        Row(
            "freight_resubmit",
            "action",
            {
                "已驳回": Cell(
                    edit=(Scope("promotion", "write"), FieldWrite("promotion", "cost_snapshot"))
                )
            },
        ),
    ),
)
MATRICES["freight"] = FREIGHT_MATRIX
