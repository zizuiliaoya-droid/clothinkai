"""谈款单的能力矩阵（流程线 5.2；PR-3 入矩阵除「时间线」「留言 / 贴图」两行外的全部行，那两行随 PR-4）。

import 本模块即把 ``MATRICES["negotiation"]`` 登记进 ``flow.matrix``；``negotiation/flow_doc.py`` import 它，
所以用到快照的地方都已注册。

列 = 7 个状态（存储值）。角色列 PR（本人）/ 主管 / 管理员 / 财务 / 运营 落到能力项上：
- 看得到 = 本人，或持 ``negotiation_all:read``（主管、财务、运营；管理员靠 ``*``）；草稿只认本人（3.2 可见范围），
  所以别的 PR、仓库整列「—」（这里就是「隐」）
- PR、主管、管理员持 ``negotiation:write``，财务、运营没有：表里「读/读/读/隐/隐」这类格用它区分
- 主管一级 = 不持 ``*``（``NoStar``）+ **显式**持 ``negotiation.review:approve``（``ExplicitScope``，
  ``negotiation.*`` 通配不算，评审 L4）+ ≠ 谈款人；老板一级 = ``negotiation_final:approve``
  （只入册不绑角色，管理员靠 ``*``）+ ≠ 谈款人 + ≠ 本轮主管（``NotFirstReviewer``）
- 收货信息：本人，或持 ``negotiation.review:approve`` 的人（主管、临时代理人；管理员靠 ``*``，N8）
- 服务费、收件三项先过字段规则（谈款复用 ``promotion.quote_amount`` / ``promotion.receiver_*``，4.6）

页级「新建谈款」不在矩阵里（矩阵没有页级行），由 service 算（偏差 8）。
改这里必须同时改设计 5.2 与 ``tests/unit/fixtures/flow_matrix_expected.py``。
"""

from __future__ import annotations

from typing import Any

from app.modules.flow.matrix import (
    MATRICES,
    AnyOf,
    Cell,
    ExplicitScope,
    FieldRead,
    FieldWrite,
    Gate,
    Matrix,
    NoStar,
    NotFirstReviewer,
    NotPrOwner,
    Owner,
    Row,
    Scope,
)
from app.modules.negotiation.enums import NegotiationStatus
from app.modules.negotiation.permissions import (
    SCOPE_NEGOTIATION,
    SCOPE_NEGOTIATION_ALL,
    SCOPE_NEGOTIATION_FINAL,
    SCOPE_NEGOTIATION_REVIEW,
)
from app.modules.promotion.exceptions import InvalidReceiverPhoneError
from app.modules.promotion.receiver import RECEIVER_FIELDS, normalize_receiver_phone

NEGOTIATION_STAGES: tuple[str, ...] = tuple(s.value for s in NegotiationStatus)
"""矩阵的列：7 个状态存储值，按 5.2 的列序（与 ``NegotiationStatus`` 的声明顺序相同）。"""

# ---------------------------------------------------------------------------
# 能力项与规则项
# ---------------------------------------------------------------------------

_WRITE = Scope(SCOPE_NEGOTIATION, "write")
_REVIEW = Scope(SCOPE_NEGOTIATION_REVIEW, "approve")
"""代录收货信息用（N8，管理员靠 ``*``）；第一级审核用下面的显式版。"""
_REVIEW_EXPLICIT = ExplicitScope(SCOPE_NEGOTIATION_REVIEW, "approve")
_FINAL = Scope(SCOPE_NEGOTIATION_FINAL, "approve")
_ALL = Scope(SCOPE_NEGOTIATION_ALL, "read")

OWNER_REASON = "只有谈款 PR 本人可以操作"
STAR_FIRST_LEVEL_REASON = "第一级由 PR 主管审核，管理员不能代做"
MANAGER_REVIEW_REASON = "需 PR 主管审核"
FINAL_REVIEW_REASON = "需老板定稿"

_OWNER = Owner(reason=OWNER_REASON)
_SEE = AnyOf(_OWNER, _ALL)
"""看得到这张谈款（草稿之外）：本人或持 ``negotiation_all:read``。"""
_OWNER_OR_REVIEWER = AnyOf(_OWNER, _REVIEW)
_NO_STAR = NoStar(reason=STAR_FIRST_LEVEL_REASON)
_NOT_NEGOTIATOR = NotPrOwner()
_NOT_FIRST_REVIEWER = NotFirstReviewer()

_QUOTE_READ = FieldRead("promotion", "quote_amount")
_QUOTE_WRITE = FieldWrite("promotion", "quote_amount")
_RECEIVER_READ = AnyOf(*(FieldRead("promotion", f) for f in RECEIVER_FIELDS))
_RECEIVER_WRITE = AnyOf(*(FieldWrite("promotion", f) for f in RECEIVER_FIELDS))

# 两级审核的「改」：看得到 + scope + 规则（顺序决定「读」的原因：先缺 scope 的用格的 reason）。
# 主管一级 NoStar 排在显式 scope 前：持 ``*`` 的人没有显式授予，排后面就会落成缺权限而不是 star_first_level
_MANAGER_EDIT = (_SEE, _NO_STAR, _REVIEW_EXPLICIT, _NOT_NEGOTIATOR)
_FINAL_EDIT = (_SEE, _FINAL, _NOT_NEGOTIATOR, _NOT_FIRST_REVIEWER)
# 收货信息的「改」：持写权限的本人或主管 / 代理人 / 管理员，且字段规则读写都过
_RECEIVER_EDIT = (_SEE, _WRITE, _OWNER_OR_REVIEWER, _RECEIVER_READ, _RECEIVER_WRITE)

MANAGER_OPINION_HINT = "提交后由 PR 主管审核"
FINAL_OPINION_HINT = "主管通过后由老板定稿"
RECEIVER_HINT = "老板定稿后填写"


def _phone_ok(doc: Any) -> bool:
    try:
        return normalize_receiver_phone(doc.receiver_phone) is not None
    except InvalidReceiverPhoneError:
        return False


# ★¹ 提交：都在编辑弹窗（保存并提交）里补，按钮不禁用。键 = 表单字段名，前端按键把缺项标回表单
SUBMIT_GATES: tuple[Gate, ...] = (
    Gate("blogger_id", "博主", in_dialog=True),
    Gate("style_id", "商品", in_dialog=True),
    Gate("goods_items", "颜色尺码", in_dialog=True, present=lambda d: d.items_complete),
    Gate("cooperation_mode", "合作模式", in_dialog=True),
    Gate("platform", "平台", in_dialog=True),
    Gate("scheduled_publish_date", "约定发布时间", in_dialog=True),
    # 服务费 0 是合法值（置换压 0；寄拍 / 送拍也可以 0，3.4），只有没有值才算缺
    Gate("quote_amount", "服务费", in_dialog=True, present=lambda d: d.quote_amount is not None),
)

# ★⁴ 确认收货信息：三项非空、电话格式；都在收货弹窗里填
RECEIVER_GATES: tuple[Gate, ...] = (
    Gate("receiver_name", "收件人", in_dialog=True),
    Gate("receiver_phone", "收件电话", in_dialog=True, present=_phone_ok),
    Gate("receiver_address", "收件地址", in_dialog=True),
)

_DRAFT = NegotiationStatus.DRAFT.value
_PENDING = NegotiationStatus.PENDING.value
_REJECTED = NegotiationStatus.REJECTED.value
_FINAL_PENDING = NegotiationStatus.FINAL_PENDING.value
_RETURNED = NegotiationStatus.FINAL_RETURNED.value
_RECEIVER_PENDING = NegotiationStatus.RECEIVER_PENDING.value
_APPROVED = NegotiationStatus.APPROVED.value


def _each(value: Cell, *stages: str) -> dict[str, Cell | tuple[Cell, ...]]:
    return dict.fromkeys(stages, value)


# ---------------------------------------------------------------------------
# 字段行
# ---------------------------------------------------------------------------

# 博主、商品、颜色尺码、模式、平台、约定发布时间、备注：草稿 改/—；老板退回 改/读/读/读/读；其余全读
_OWNER_EDITS = Cell(edit=(_WRITE, _OWNER), read=(_SEE,))
_basic: dict[str, Cell | tuple[Cell, ...]] = {
    _DRAFT: Cell(edit=(_WRITE, _OWNER), read=(_OWNER,)),
    _RETURNED: _OWNER_EDITS,
    **_each(Cell(read=(_SEE,)), _PENDING, _REJECTED, _FINAL_PENDING, _RECEIVER_PENDING, _APPROVED),
}

# 服务费：同上，但运营「隐」（字段规则 promotion.quote_amount 不含运营）
_QUOTE_EDIT = (_WRITE, _OWNER, _QUOTE_READ, _QUOTE_WRITE)
_quote_amount: dict[str, Cell | tuple[Cell, ...]] = {
    _DRAFT: Cell(edit=_QUOTE_EDIT, read=(_OWNER, _QUOTE_READ)),
    _RETURNED: Cell(edit=_QUOTE_EDIT, read=(_SEE, _QUOTE_READ)),
    **_each(
        Cell(read=(_SEE, _QUOTE_READ)),
        _PENDING,
        _REJECTED,
        _FINAL_PENDING,
        _RECEIVER_PENDING,
        _APPROVED,
    ),
}

# 主管审核意见：草稿 灰/—；主管待审 读/改/读/读/读；其余全读（老板退回时是上一轮的）
_manager_opinion: dict[str, Cell | tuple[Cell, ...]] = {
    _DRAFT: Cell(grey=(_OWNER,), hint=MANAGER_OPINION_HINT),
    _PENDING: Cell(edit=_MANAGER_EDIT, read=(_SEE,)),
    **_each(Cell(read=(_SEE,)), _REJECTED, _FINAL_PENDING, _RETURNED, _RECEIVER_PENDING, _APPROVED),
}

# 老板意见：草稿 灰/—；主管待审 全灰；审核驳回 隐；待老板审核 读/读/改/读/读；其余全读
_final_opinion: dict[str, Cell | tuple[Cell, ...]] = {
    _DRAFT: Cell(grey=(_OWNER,), hint=FINAL_OPINION_HINT),
    _PENDING: Cell(grey=(_SEE,), hint=FINAL_OPINION_HINT),
    _FINAL_PENDING: Cell(edit=_FINAL_EDIT, read=(_SEE,)),
    **_each(Cell(read=(_SEE,)), _RETURNED, _RECEIVER_PENDING, _APPROVED),
}

# 收货三项：草稿 灰/—；主管待审 / 待老板审核 / 老板退回 灰/灰/灰/隐/隐；审核驳回 隐；
# 待补收货地址 改/改/改/隐/隐；已定稿 读/读/读/隐/隐（财务、运营过不了字段规则）
_receiver: dict[str, Cell | tuple[Cell, ...]] = {
    _DRAFT: Cell(grey=(_OWNER, _RECEIVER_READ), hint=RECEIVER_HINT),
    **_each(
        Cell(grey=(_SEE, _RECEIVER_READ), hint=RECEIVER_HINT), _PENDING, _FINAL_PENDING, _RETURNED
    ),
    _RECEIVER_PENDING: Cell(edit=_RECEIVER_EDIT, read=(_SEE, _RECEIVER_READ)),
    _APPROVED: Cell(read=(_SEE, _RECEIVER_READ)),
}

# 超 36 小时高亮：只在主管待审，读/读/读/隐/隐（财务、运营没有 negotiation:write）
_overdue_badge: dict[str, Cell | tuple[Cell, ...]] = {_PENDING: Cell(read=(_SEE, _WRITE))}

# 「同博主有别的 PR 在谈」：草稿与老板退回，只给本人（编辑弹窗里的提示）
_others_in_progress: dict[str, Cell | tuple[Cell, ...]] = _each(
    Cell(read=(_OWNER,)), _DRAFT, _RETURNED
)

# ---------------------------------------------------------------------------
# 动作行（状态机在 NegotiationDoc.allowed_actions）
# ---------------------------------------------------------------------------

# 编辑 / 提交：草稿 改/—；老板退回 改/读/读/隐/隐
_edit: dict[str, Cell | tuple[Cell, ...]] = {
    _DRAFT: Cell(edit=(_WRITE, _OWNER)),
    _RETURNED: Cell(edit=(_WRITE, _OWNER), read=(_SEE, _WRITE)),
}
_submit: dict[str, Cell | tuple[Cell, ...]] = {
    _DRAFT: Cell(edit=(_WRITE, _OWNER), gates=SUBMIT_GATES),
    _RETURNED: Cell(edit=(_WRITE, _OWNER), read=(_SEE, _WRITE), gates=SUBMIT_GATES),
}

# 主管通过 / 驳回：主管待审 读/改★²/读/隐/隐（管理员读：NoStar；主管审自己建的：读，self_review）
_manager_review: dict[str, Cell | tuple[Cell, ...]] = {
    _PENDING: Cell(edit=_MANAGER_EDIT, read=(_SEE, _WRITE), reason=MANAGER_REVIEW_REASON),
}

# 老板定稿 / 退回：待老板审核 读/读/改★³/隐/隐
_final_review: dict[str, Cell | tuple[Cell, ...]] = {
    _FINAL_PENDING: Cell(edit=_FINAL_EDIT, read=(_SEE, _WRITE), reason=FINAL_REVIEW_REASON),
}

# 保存 / 确认收货信息：待补收货地址 改(★⁴)/改/改/隐/隐
_receiver_save: dict[str, Cell | tuple[Cell, ...]] = {_RECEIVER_PENDING: Cell(edit=_RECEIVER_EDIT)}
_receiver_confirm: dict[str, Cell | tuple[Cell, ...]] = {
    _RECEIVER_PENDING: Cell(edit=_RECEIVER_EDIT, gates=RECEIVER_GATES),
}

# 复制为新谈款：审核驳回 改/隐/隐/隐/隐（11-4：只放在审批未通过上）
_copy: dict[str, Cell | tuple[Cell, ...]] = {_REJECTED: Cell(edit=(_WRITE, _OWNER))}

NEGOTIATION_MATRIX = Matrix(
    kind="negotiation",
    stages=NEGOTIATION_STAGES,
    rows=(
        Row("edit", "action", _edit),
        Row("submit", "action", _submit),
        Row("manager_review", "action", _manager_review),
        Row("final_review", "action", _final_review),
        Row("receiver_save", "action", _receiver_save),
        Row("receiver_confirm", "action", _receiver_confirm),
        Row("copy", "action", _copy),
        Row("basic", "field", _basic),
        Row("quote_amount", "field", _quote_amount),
        Row("manager_opinion", "field", _manager_opinion),
        Row("final_opinion", "field", _final_opinion),
        Row("receiver", "field", _receiver),
        Row("overdue_badge", "field", _overdue_badge),
        Row("others_in_progress", "field", _others_in_progress),
    ),
)

MATRICES.update({"negotiation": NEGOTIATION_MATRIX})

__all__ = [
    "FINAL_OPINION_HINT",
    "FINAL_REVIEW_REASON",
    "MANAGER_OPINION_HINT",
    "MANAGER_REVIEW_REASON",
    "NEGOTIATION_MATRIX",
    "NEGOTIATION_STAGES",
    "OWNER_REASON",
    "RECEIVER_GATES",
    "RECEIVER_HINT",
    "STAR_FIRST_LEVEL_REASON",
    "SUBMIT_GATES",
]
