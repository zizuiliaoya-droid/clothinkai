"""结款单的能力矩阵（流程线 5.3「结款单：追加项、填实付、驳回、上传凭证」一行按结款状态展开；PR-6）。

import 本模块即把 ``MATRICES["settlement"]`` 登记进 ``flow.matrix``；``finance/flow_doc.py`` import 它，
所以用到快照的地方都已注册。

列 = 5 个结款状态（存储值）；只有动作行（结款页按行级 ``ui.actions`` 渲染，8.6），没有字段行。
状态机不在这里：``SettlementDoc.allowed_actions`` 由 ``SettlementStatusMachine`` 推导（计划 D10），
service 的转移仍走状态机 + 现有 scope / 字段判断、不调 ``require()``，所以每格的能力项照 service 现在的判断抄：
- 核查（F0，旧单）/ 驳回（F5）：路由 ``settlement.review:approve``（主管、财务、管理员）+ service 的自审拒绝
  （结款单 ``pr_id`` = 本人 →「读」，原因同 ``SelfReviewForbiddenError``）
- 追加项（F2）/ 填实付（F3）：路由 ``settlement:write`` + 字段写 ``settlement.payment_amount``（主管、财务、管理员）
- 上传凭证（F4）：路由 ``settlement.pay:upload_proof`` + service 判 ``finance.settlement:pay``（财务、管理员；主管不能，¹⁶）
PR、运营没有任何 settlement scope，进不了结款页，整列「隐」。
改这里必须同时改设计 5.3 与 ``tests/unit/fixtures/flow_matrix_expected.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from app.modules.finance.enums import SettlementStatus
from app.modules.flow.matrix import (
    MATRICES,
    Cell,
    FieldWrite,
    FlowActor,
    FlowDocBase,
    Matrix,
    Row,
    Rule,
    Scope,
)

SETTLEMENT_STAGES: tuple[str, ...] = tuple(s.value for s in SettlementStatus)
"""矩阵的列：5 个结款状态存储值（``SettlementStatus`` 的声明顺序）。"""

SELF_REVIEW_REASON = "不能审核自己提交的推广对应的结款单"


@dataclass(frozen=True)
class NotSubmitter(Rule):
    """≠ 结款单的提交人（``pr_id``，来自推广单负责 PR）：同 ``SettlementService.review`` 的自审拒绝。

    结款单没有 ``pr_id``（导入的旧单，FK SET NULL）时没有可比的人，放行（service 同判）。
    """

    reason: str = SELF_REVIEW_REASON
    rule: ClassVar[str] = "self_review"

    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool:
        return doc.owner_id is None or doc.owner_id != actor.user_id


_REVIEW = Scope("settlement.review", "approve")
_WRITE = Scope("settlement", "write")
_AMOUNT_WRITE = FieldWrite("settlement", "payment_amount")
_UPLOAD_PROOF = Scope("settlement.pay", "upload_proof")
_FINANCE_PAY = Scope("finance.settlement", "pay")
_NOT_SUBMITTER = NotSubmitter()

_PENDING_REVIEW = SettlementStatus.PENDING_REVIEW.value
_PENDING_PAYMENT = SettlementStatus.PENDING_PAYMENT.value
_PENDING_FINANCE = SettlementStatus.PENDING_FINANCE.value

# 核查 / 驳回：有审核权的人「改」，自己提交的「读」
_REVIEW_CELL = Cell(edit=(_REVIEW, _NOT_SUBMITTER), read=(_REVIEW,))
# 追加项 / 填实付：主管、财务、管理员（¹⁶、★¹²；实付 ≠ 应付的调整说明在弹窗里填，service 判）
_AMOUNT_CELL = Cell(edit=(_WRITE, _AMOUNT_WRITE))

SETTLEMENT_MATRIX = Matrix(
    kind="settlement",
    stages=SETTLEMENT_STAGES,
    rows=(
        # F0：只给上线前停在待核查的旧单
        Row("legacy_approve", "action", {_PENDING_REVIEW: _REVIEW_CELL}),
        # F5：待付款 / 待财务付款
        Row("reject", "action", {_PENDING_PAYMENT: _REVIEW_CELL, _PENDING_FINANCE: _REVIEW_CELL}),
        Row("extra_item", "action", {_PENDING_PAYMENT: _AMOUNT_CELL}),
        Row("fill_payment", "action", {_PENDING_PAYMENT: _AMOUNT_CELL}),
        # F4：凭证必传、付款日期 = 当天（★¹²，弹窗里传）
        Row("upload_proof", "action", {_PENDING_FINANCE: Cell(edit=(_UPLOAD_PROOF, _FINANCE_PAY))}),
    ),
)

MATRICES.update({"settlement": SETTLEMENT_MATRIX})

__all__ = ["SELF_REVIEW_REASON", "SETTLEMENT_MATRIX", "SETTLEMENT_STAGES", "NotSubmitter"]
