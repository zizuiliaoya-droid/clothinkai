"""推广单 / 仓库行的能力矩阵（流程线 5.3；PR-2 有收件、颜色尺码明细、发货信息三类字段行与
推送 / 撤回 / 纳入发货 / 回填四个动作行；PR-4 加「时间线」字段行与「留言 / 贴图」动作行；
PR-5 加商品归属、合作信息、合作模式、服务费四个字段行与催发、标记完成、停用三个动作行；
PR-6 加 7 天数据、收款码、结款单、结款聊天截图四个字段行与推广审核、结款重提、上传结款聊天截图、录 7 天数据四个动作行）。

import 本模块即把 ``MATRICES["promotion"]`` 与 ``MATRICES["warehouse"]`` 登记进 ``flow.matrix``；
``promotion/flow_doc.py`` import 它，所以用到快照的地方都已注册。

列（A ~ H）是文档写法，代码里按 ``stage_calculator.PROMOTION_STAGES`` 的 22 个阶段逐格展开（走不到的阶段按列填）。
角色列 PR / 主管 / 管理员 / 财务 / 运营 / 仓库 落到能力项上：
- PR、主管、管理员 都持 ``promotion:write``（PR 与主管靠 ``promotion.*:*``，管理员靠 ``*``）；运营只有 ``promotion.*:read``
- 主管 = ``promotion_ship:push``（PR 没有：推送仓库「需管理员或 PR 主管确认」）；仓库 = ``promotion_ship:fill``，没有 ``promotion:read``（060 收回）
- 财务能读的格用 ``finance.settlement:read``；「只有管理员改」用 ``Star()``
- PR-5 起「主管改、PR 读」的格（合作信息由谈款带过来，11-16）= ``promotion:write`` 且 ``promotion_create:write``（独立一级域，
  PR 的 ``promotion.*:*`` 命中不了；临时授了建单权的代理人同时能改合作信息）
- 收件三项先过字段规则（4.6）：读要 ``FieldRead``、改要 ``FieldWrite``（三项任一即可，逐项的写权限由 service 再判）

改这里必须同时改设计 5.3 与 ``tests/unit/fixtures/flow_matrix_expected.py``。
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from app.modules.flow.matrix import (
    MATRICES,
    AnyOf,
    Cell,
    FieldRead,
    FieldWrite,
    Gate,
    Matrix,
    ModeIn,
    NotPrOwner,
    PromotionSettlementIn,
    PublishIn,
    RetroIn,
    Row,
    Scope,
    SettlementIn,
    ShipIn,
    Star,
)
from app.modules.promotion.enums import CooperationMode, PublishStatus, RecallStatus
from app.modules.promotion.exceptions import InvalidReceiverPhoneError
from app.modules.promotion.recall_ui_rules import NoOpenFreight, PaymentQrEditable, RecallEditable
from app.modules.promotion.receiver import RECEIVER_FIELDS, normalize_receiver_phone
from app.modules.promotion.stage_calculator import PROMOTION_STAGES, STAGE_COLUMN

# ---------------------------------------------------------------------------
# 能力项
# ---------------------------------------------------------------------------

_WRITE = Scope("promotion", "write")
_READ = Scope("promotion", "read")
_PUSH = Scope("promotion_ship", "push")
_FILL = Scope("promotion_ship", "fill")
_FINANCE_READ = Scope("finance.settlement", "read")
_CREATE = Scope("promotion_create", "write")
_URGE_WRITE = Scope("promotion.urge", "write")
_STAR = Star()
# 「主管改、PR 读」（PR-5）
_MANAGER_EDIT = (_WRITE, _CREATE)
_QUOTE_READ = FieldRead("promotion", "quote_amount")
_QUOTE_WRITE = FieldWrite("promotion", "quote_amount")

# PR-6：推广审核（独立一级域，PR 的 promotion.*:* 命中不了）、录 7 天数据、收款码（含字段规则，财务能读）
_REVIEW = Scope("promotion_review", "approve")
_METRICS_WRITE = Scope("promotion.metrics", "write")
_QR_SCOPE = Scope("promotion.payment_qr", "write")
_QR_READ = FieldRead("promotion", "payment_qr")
_QR_WRITE = FieldWrite("promotion", "payment_qr")
# 结款单的追加项 / 实付（主管、财务、管理员，3.4 F2 / F3）
_SETTLEMENT_WRITE = FieldWrite("settlement", "payment_amount")

_RECEIVER_READ = AnyOf(*(FieldRead("promotion", f) for f in RECEIVER_FIELDS))
_RECEIVER_WRITE = AnyOf(*(FieldWrite("promotion", f) for f in RECEIVER_FIELDS))
# 「读」里有仓库的格：仓库没有 promotion:read，只在仓库页看推送过的单
_READ_OR_WAREHOUSE = AnyOf(_READ, _FILL)
_READ_OR_WAREHOUSE_OR_FINANCE = AnyOf(_READ, _FILL, _FINANCE_READ)
_READ_OR_FINANCE = AnyOf(_READ, _FINANCE_READ)

SHIP_PUSH_REASON = "需管理员或 PR 主管确认"
SHIPPING_GREY_HINT = "推送后由仓库回填"
REVIEW_READ_REASON = "需 PR 主管审核"
PAYMENT_QR_GREY_HINT = "发布后上传"
SETTLEMENT_GREY_HINT = "主管审核通过后生成"
SETTLEMENT_CHAT_GREY_HINT = "财务付款后上传"
METRICS_SETTLE_HINT = "已结款后可录"


def metrics_wait_hint(doc: Any) -> str:
    """⁸「YYYY-MM-DD 起可录」：待满 7 天才用得到，那时一定有发布日；万一没有给句不带日期的。"""
    available = doc.metrics_available_from
    return f"{available.isoformat()} 起可录" if available is not None else "满 7 天后可录"


def _phone_ok(doc: Any) -> bool:
    try:
        return normalize_receiver_phone(doc.receiver_phone) is not None
    except InvalidReceiverPhoneError:
        return False


# 推送仓库 ★¹⁴：收件三项非空、电话合格、颜色尺码齐；都能在推送弹窗里补（in_dialog）
SHIP_PUSH_GATES: tuple[Gate, ...] = (
    Gate("receiver_name", "收件人", in_dialog=True),
    Gate("receiver_phone", "收件电话", in_dialog=True, present=_phone_ok),
    Gate("receiver_address", "收件地址", in_dialog=True),
    Gate("goods_items", "颜色尺码", in_dialog=True, present=lambda d: d.items_complete),
)


def _returned_ok(doc: Any) -> bool:
    """R2 ④：寄拍要有寄回单号，或召回成功且有召回寄回截图；其余模式不要求。"""
    if doc.cooperation_mode != CooperationMode.CONSIGNMENT.value:
        return True
    if (doc.return_waybill or "").strip():
        return True
    return bool(doc.recall_status == RecallStatus.RECALLED_SUCCESS.value and doc.has_recall_waybill)


def _review_qr_ok(doc: Any) -> bool:
    """R2 ⑤：应付（审核时 = 服务费）> 0 才要收款码。"""
    return (doc.quote_amount or Decimal(0)) <= 0 or doc.has_payment_qr


# 推广审核 ★¹⁵：一个动作里有通过和驳回，缺项只卡通过（D11：require(gates=False)，通过再 ensure_gates），
# 所以按钮不禁用（in_dialog），弹窗里按 missing 禁用「通过」
REVIEW_GATES: tuple[Gate, ...] = (
    Gate("return_waybill", "寄回单号", in_dialog=True, present=_returned_ok),
    Gate("payment_qr", "收款码", in_dialog=True, present=_review_qr_ok),
)
# 结款重提 ★¹⁷：有收款码（重提弹窗里补传）；说明必填由请求体校验
SETTLEMENT_RESUBMIT_GATES: tuple[Gate, ...] = (
    Gate("payment_qr", "收款码", in_dialog=True, present=lambda d: d.has_payment_qr),
)

# ---------------------------------------------------------------------------
# 列 → 阶段
# ---------------------------------------------------------------------------


def _stages(*columns: str) -> tuple[str, ...]:
    return tuple(s for s in PROMOTION_STAGES if STAGE_COLUMN[s] in columns)


def _fill(
    cells: dict[str, Cell | tuple[Cell, ...]], columns: str, value: Cell | tuple[Cell, ...]
) -> None:
    for stage in _stages(*columns):
        cells[stage] = value


def _history_by_a(a: Cell, other: Cell) -> tuple[Cell, Cell]:
    """C ~ F 列：历史单（发货为空）按 A 列取值（5.3「历史单」），其余按本列。"""
    return (replace(a, when=(ShipIn(None),)), other)


# ---------------------------------------------------------------------------
# 字段行
# ---------------------------------------------------------------------------

# 收件三项：A 改/改/改/隐/隐/隐；B 改/改/改/隐/隐/读；C ~ H 读/读/改/隐/隐/读（「已发货」之后只有管理员能改）
# 改也要读得到（7.1：读不到的字段所在分组不出现在 fields / edits）
_RECEIVER_A = Cell(edit=(_WRITE, _RECEIVER_READ, _RECEIVER_WRITE), read=(_READ, _RECEIVER_READ))
_RECEIVER_B = Cell(
    edit=(_WRITE, _RECEIVER_READ, _RECEIVER_WRITE), read=(_READ_OR_WAREHOUSE, _RECEIVER_READ)
)
_RECEIVER_LATER = Cell(
    edit=(_STAR, _RECEIVER_READ, _RECEIVER_WRITE), read=(_READ_OR_WAREHOUSE, _RECEIVER_READ)
)

_receiver: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_receiver, "A", _RECEIVER_A)
_fill(_receiver, "B", _RECEIVER_B)
_fill(_receiver, "CDEF", _history_by_a(_RECEIVER_A, _RECEIVER_LATER))
_fill(_receiver, "GH", _RECEIVER_LATER)

# 颜色尺码明细：A 读/改★/改/隐/读/隐；B ~ D 读/读/改/隐/读/读；E ~ H 读/读/改/读/读/读
_ITEMS_A = Cell(edit=(_PUSH,), read=(_READ,))
_ITEMS_LATER = Cell(edit=(_STAR,), read=(_READ_OR_WAREHOUSE,))
_ITEMS_SETTLING = Cell(edit=(_STAR,), read=(_READ_OR_WAREHOUSE_OR_FINANCE,))

_goods_items: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_goods_items, "A", _ITEMS_A)
_fill(_goods_items, "B", _ITEMS_LATER)
_fill(_goods_items, "CD", _history_by_a(_ITEMS_A, _ITEMS_LATER))
_fill(_goods_items, "EF", _history_by_a(_ITEMS_A, _ITEMS_SETTLING))
_fill(_goods_items, "GH", _ITEMS_SETTLING)

# 发货信息：A 灰（推送后由仓库回填）；B ~ G 读/读/改/隐/读/改；H 读/读/改/隐/读/读。历史单不按 A 列（走「纳入发货」）
_SHIPPING_FILLABLE = Cell(edit=(_FILL,), read=(_READ_OR_WAREHOUSE,))

_shipping: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_shipping, "A", Cell(grey=(_READ,), hint=SHIPPING_GREY_HINT))
_fill(_shipping, "BCDEFG", _SHIPPING_FILLABLE)
_fill(_shipping, "H", Cell(edit=(_STAR,), read=(_READ_OR_WAREHOUSE,)))

# 商品归属（PR-5，¹⁹）：A 读/改/改/隐/读/隐；B ~ D 读/改/改/隐/读/读；E ~ G 读/改/改/读/读/读；H 读/读/改/读/读/读；
# 历史单按 A 列
_GOODS_A = Cell(edit=_MANAGER_EDIT, read=(_READ,))
_GOODS_SHIPPED = Cell(edit=_MANAGER_EDIT, read=(_READ_OR_WAREHOUSE,))
_GOODS_SETTLING = Cell(edit=_MANAGER_EDIT, read=(_READ_OR_WAREHOUSE_OR_FINANCE,))

_goods: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_goods, "A", _GOODS_A)
_fill(_goods, "B", _GOODS_SHIPPED)
_fill(_goods, "CD", _history_by_a(_GOODS_A, _GOODS_SHIPPED))
_fill(_goods, "EF", _history_by_a(_GOODS_A, _GOODS_SETTLING))
_fill(_goods, "G", _GOODS_SETTLING)
_fill(_goods, "H", Cell(edit=(_STAR,), read=(_READ_OR_WAREHOUSE_OR_FINANCE,)))

# 合作信息（平台、约定发布时间、备注；内容分类随 PR-9）：A ~ D 读/改/改/隐/读/隐；E ~ H 读/读/改/读/读/隐
_cooperation: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_cooperation, "ABCD", Cell(edit=_MANAGER_EDIT, read=(_READ,)))
_fill(_cooperation, "EFGH", Cell(edit=(_STAR,), read=(_READ_OR_FINANCE,)))

_platform_posts: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_platform_posts, "ABC", Cell(grey=(_READ,), hint="发布后可录其他平台"))
_fill(_platform_posts, "DEF", Cell(edit=(_WRITE,), read=(_READ,)))
_fill(_platform_posts, "G", Cell(edit=(_WRITE,), read=(_READ,), when=(PublishIn("已发布"),)))
_fill(_platform_posts, "H", Cell(edit=(_STAR,), read=(_READ,)))

# 合作模式（²³）：有值所有人读；空值主管 / 管理员可补一次（A ~ G），H 只有管理员。PR 一律读
_MODE_EMPTY = (ModeIn(None),)
_cooperation_mode: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(
    _cooperation_mode,
    "ABCD",
    (Cell(edit=_MANAGER_EDIT, read=(_READ,), when=_MODE_EMPTY), Cell(read=(_READ,))),
)
_fill(
    _cooperation_mode,
    "EFG",
    (
        Cell(edit=_MANAGER_EDIT, read=(_READ_OR_FINANCE,), when=_MODE_EMPTY),
        Cell(read=(_READ_OR_FINANCE,)),
    ),
)
_fill(
    _cooperation_mode,
    "H",
    (
        Cell(edit=(_STAR,), read=(_READ_OR_FINANCE,), when=_MODE_EMPTY),
        Cell(read=(_READ_OR_FINANCE,)),
    ),
)

# 服务费：A ~ D 读/改/改/隐/隐/隐；E、F、H 读/读/读/读/隐/隐（结款单生成后连管理员也只读）；G 读/读/读/隐/隐/隐。
# 运营过不了字段规则 promotion.quote_amount
_quote_amount: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(
    _quote_amount,
    "ABCD",
    Cell(edit=(*_MANAGER_EDIT, _QUOTE_READ, _QUOTE_WRITE), read=(_READ, _QUOTE_READ)),
)
_fill(_quote_amount, "EFH", Cell(read=(_READ_OR_FINANCE, _QUOTE_READ)))
_fill(_quote_amount, "G", Cell(read=(_READ, _QUOTE_READ)))

# 时间线（PR-4）：A ~ D 读/读/读/—/读/—；E ~ H 财务也读（结款组，事件按分组的过滤在读接口，6.5）；仓库页不放时间线
_timeline: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_timeline, "ABCD", Cell(read=(_READ,)))
_fill(_timeline, "EFGH", Cell(read=(AnyOf(_READ, _FINANCE_READ),)))

# 7 天数据（PR-6，⁸ + D13）：没结款（A ~ E）灰「已结款后可录」、待满 7 天灰「YYYY-MM-DD 起可录」，运营同灰；
# 待录 7 天数据 改/改/改/隐/读/隐；录过之后（待复盘）与 H 列只有管理员改；G 隐
_metrics: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_metrics, "ABCDE", Cell(grey=(_READ,), hint=METRICS_SETTLE_HINT))
_metrics["待满 7 天"] = Cell(grey=(_READ,), hint=metrics_wait_hint)
_metrics["待录 7 天数据"] = Cell(edit=(_METRICS_WRITE,), read=(_READ,))
_metrics["待复盘"] = Cell(edit=(_STAR,), read=(_READ,))
_fill(_metrics, "H", Cell(edit=(_STAR,), read=(_READ,)))

# 收款码（PR-6，⁹ + 计划 D9；规则同 7.3 收款码接口，持 * 只受 ① 限制）：
# ① 没发布（A ~ C）灰「发布后上传」，管理员也一样；D 改/改/改/隐/隐/隐；
# ② 结款单 = 待财务付款 锁；③ 推广单 已付款 / 已结款 锁（召回中的例外随 PR-8，所以 G 列也按它拆）；
# 锁 = 读/读/改/读/隐/隐；F、H 列默认锁；①' 之外，没发布过就停用的单连管理员也只读（①）
_QR_EDITABLE = Cell(edit=(_QR_SCOPE, _QR_READ, _QR_WRITE), read=(_READ_OR_FINANCE, _QR_READ))
_QR_LOCKED = Cell(edit=(_STAR,), read=(_READ_OR_FINANCE, _QR_READ))
_FINANCE_FILLED = (SettlementIn("待财务付款"),)

_payment_qr: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_payment_qr, "ABC", Cell(grey=(_WRITE, _QR_READ), hint=PAYMENT_QR_GREY_HINT))
_fill(_payment_qr, "D", Cell(edit=(_QR_SCOPE, _QR_READ, _QR_WRITE), read=(_READ, _QR_READ)))
_payment_qr["待财务付款"] = (replace(_QR_LOCKED, when=_FINANCE_FILLED), _QR_EDITABLE)
_payment_qr["结款驳回 · 待重提"] = _QR_EDITABLE
_payment_qr["待传结款截图"] = _QR_LOCKED
_fill(_payment_qr, "F", _QR_LOCKED)
_fill(
    _payment_qr,
    "G",
    (
        replace(_QR_LOCKED, when=_FINANCE_FILLED),
        replace(_QR_LOCKED, when=(PromotionSettlementIn("已付款", "已结款"),)),
        _QR_EDITABLE,
    ),
)
_fill(
    _payment_qr,
    "H",
    (
        Cell(
            read=(_READ_OR_FINANCE, _QR_READ),
            when=(PublishIn(PublishStatus.UNPUBLISHED.value, PublishStatus.ABNORMAL.value),),
        ),
        _QR_LOCKED,
    ),
)

# 结款单（PR-6，¹²：应付、追加项、实付、调整说明、付款日期、凭证）：金额跟服务费的字段规则走（运营、仓库看不到）。
# D 灰「主管审核通过后生成」；待财务付款 读/改/改/改/隐/隐（主管加追加项、填实付、驳回，财务付款）；
# 驳回之后、付款之后（11-63「付款后对所有人只读」）与 F、H 列 读/读/读/读/隐/隐
_SETTLEMENT_READ = (_READ_OR_FINANCE, _QUOTE_READ)
_settlement: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_settlement, "D", Cell(grey=(_READ, _QUOTE_READ), hint=SETTLEMENT_GREY_HINT))
_settlement["待财务付款"] = Cell(edit=(_SETTLEMENT_WRITE, _QUOTE_READ), read=_SETTLEMENT_READ)
_settlement["结款驳回 · 待重提"] = Cell(read=_SETTLEMENT_READ)
_settlement["待传结款截图"] = Cell(read=_SETTLEMENT_READ)
_fill(_settlement, "FH", Cell(read=_SETTLEMENT_READ))

# 结款聊天截图（PR-6，¹³）：跟收款码的字段规则（7.3 settlement_chat_signed_url）。
# 财务付款前灰「财务付款后上传」（财务同灰）；待传结款截图 改/改/改/读/隐/隐；之后（F、H）读/读/改/读/隐/隐
_CHAT_READ = (_READ_OR_FINANCE, _QR_READ)
_settlement_chat: dict[str, Cell | tuple[Cell, ...]] = {}
for _stage in ("待财务付款", "结款驳回 · 待重提"):
    _settlement_chat[_stage] = Cell(grey=_CHAT_READ, hint=SETTLEMENT_CHAT_GREY_HINT)
_settlement_chat["待传结款截图"] = Cell(edit=(_WRITE, _QR_READ), read=_CHAT_READ)
_fill(_settlement_chat, "FH", Cell(edit=(_STAR,), read=_CHAT_READ))

# ---------------------------------------------------------------------------
# 动作行（状态机在 PromotionDoc / WarehouseDoc.allowed_actions）
# ---------------------------------------------------------------------------

_ship_push: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(
    _ship_push,
    "A",
    Cell(edit=(_PUSH,), read=(_WRITE,), reason=SHIP_PUSH_REASON, gates=SHIP_PUSH_GATES),
)

_ship_withdraw: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_ship_withdraw, "B", Cell(edit=(_PUSH,), read=(_WRITE,), reason=SHIP_PUSH_REASON))

# 纳入发货：只对发货为空的历史单（状态机判），C 列 隐/改/改/隐/隐/隐
_ship_include: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_ship_include, "C", Cell(edit=(_PUSH,)))

# 仓库回填 / 改单号：B ~ G 隐/隐/改/隐/隐/改；H 只有管理员
_ship_fill: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_ship_fill, "BCDEFG", Cell(edit=(_FILL,)))
_fill(_ship_fill, "H", Cell(edit=(_STAR,)))

# 留言 / 贴图（PR-4）：全阶段 改/改/改/隐/隐/隐（运营只读、财务与仓库不进推广页）
_comment: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_comment, "ABCDEFGH", Cell(edit=(_WRITE,)))

# 催发：手动催、标记完成（PR-5）：A ~ C 改/改/改/隐/隐/隐。标记完成的状态在催发任务上，不进推广单状态机
_urge: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_urge, "ABC", Cell(edit=(_URGE_WRITE,)))

# 停用推广单（PR-5，11-15）：A ~ D、G 隐/改/改/隐/隐/隐；结款单生成后（E、F）与 H 只有管理员
_deactivate: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_deactivate, "ABCDG", Cell(edit=(_CREATE, NoOpenFreight()), read=(_CREATE,)))
_fill(_deactivate, "EFH", Cell(edit=(_STAR, NoOpenFreight()), read=(_STAR,)))

# 推广审核通过 / 驳回（PR-6，★¹⁵）：D 读/改★/改/隐/隐/隐，审核人 ≠ 谈款人；PR 读「需 PR 主管审核」。
# 只在待主管审核时状态机允许（推广驳回时隐）
_review: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(
    _review,
    "D",
    Cell(
        edit=(_REVIEW, NotPrOwner()),
        read=(_WRITE,),
        reason=REVIEW_READ_REASON,
        gates=REVIEW_GATES,
    ),
)

# 结款重提（PR-6，★¹⁷）：E 改/改/改/隐/隐/隐，只在结款驳回时状态机允许
_settlement_resubmit: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_settlement_resubmit, "E", Cell(edit=(_WRITE,), gates=SETTLEMENT_RESUBMIT_GATES))

# 上传结款聊天截图（PR-6，★¹³）：E 改/改/改/隐/隐/隐，只在待传结款截图时允许；之前灰「财务付款后上传」
_settlement_chat_action: dict[str, Cell | tuple[Cell, ...]] = {}
for _stage in ("待财务付款", "结款驳回 · 待重提"):
    _settlement_chat_action[_stage] = Cell(
        edit=(_WRITE,), grey=(_WRITE,), hint=SETTLEMENT_CHAT_GREY_HINT
    )
_settlement_chat_action["待传结款截图"] = Cell(edit=(_WRITE,))

# 录 7 天数据（PR-6，★⁸）：D、E 灰「已结款后可录」；F 改/改/改/隐/隐/隐，只在待录 7 天数据时允许——
# 待满 7 天灰「YYYY-MM-DD 起可录」，录过之后（待复盘）隐
_metrics_action: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_metrics_action, "DE", Cell(grey=(_METRICS_WRITE,), hint=METRICS_SETTLE_HINT))
_metrics_action["待满 7 天"] = Cell(
    edit=(_METRICS_WRITE,), grey=(_METRICS_WRITE,), hint=metrics_wait_hint
)
_metrics_action["待录 7 天数据"] = Cell(edit=(_METRICS_WRITE,))
_metrics_action["待复盘"] = Cell(edit=(_METRICS_WRITE,))

# 复盘与结款并行：主管写完即完成，完成版只允许真正*修订。
_retro: dict[str, Cell | tuple[Cell, ...]] = {}

_fill(
    _retro,
    "DEF",
    (
        Cell(edit=(Scope("promotion_retro", "write"),), read=(_READ,), when=(RetroIn("待复盘"),)),
        Cell(edit=(_STAR,), read=(_READ,)),
    ),
)
_fill(_retro, "H", Cell(edit=(_STAR,), read=(_READ,)))
_fill(
    _retro,
    "G",
    (
        Cell(
            edit=(Scope("promotion_retro", "write"),),
            read=(_READ,),
            when=(PublishIn("已发布"), RetroIn("待复盘")),
        ),
        Cell(edit=(_STAR,), read=(_READ,), when=(PublishIn("已发布"),)),
    ),
)
_retro_write: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(
    _retro_write,
    "DEF",
    Cell(edit=(Scope("promotion_retro", "write"),), read=(_WRITE,), reason="由主管写复盘"),
)
_retro_revise: dict[str, Cell | tuple[Cell, ...]] = {}
_fill(_retro_revise, "DEFH", Cell(edit=(_STAR,)))
_fill(
    _retro_write,
    "G",
    Cell(
        edit=(Scope("promotion_retro", "write"),),
        read=(_WRITE,),
        reason="由主管写复盘",
        when=(PublishIn("已发布"),),
    ),
)
_fill(_retro_revise, "G", Cell(edit=(_STAR,), when=(PublishIn("已发布"),)))

# PR8：二维码的管理员特例不适用于寄回图；专用判断使用完整状态快照。
for _stage in PROMOTION_STAGES:
    if STAGE_COLUMN[_stage] not in "ABC":
        _payment_qr[_stage] = Cell(
            edit=(_QR_SCOPE, _QR_READ, _QR_WRITE, PaymentQrEditable()),
            read=(_READ if STAGE_COLUMN[_stage] == "D" else _READ_OR_FINANCE, _QR_READ),
        )

_recall_start: dict[str, Cell | tuple[Cell, ...]] = {}
_recall_waybill: dict[str, Cell | tuple[Cell, ...]] = {}
_recall_result: dict[str, Cell | tuple[Cell, ...]] = {}
_freight_submit: dict[str, Cell | tuple[Cell, ...]] = {}
_recall_info: dict[str, Cell | tuple[Cell, ...]] = {}
_freight_payment: dict[str, Cell | tuple[Cell, ...]] = {}
for _stage in PROMOTION_STAGES:
    _recall_start[_stage] = Cell(edit=(_WRITE, RecallEditable("start")))
    _recall_waybill[_stage] = Cell(edit=(_WRITE, _QR_WRITE, RecallEditable("waybill")))
    _recall_result[_stage] = Cell(edit=(_WRITE, RecallEditable("result")))
    _freight_submit[_stage] = Cell(
        edit=(_WRITE, FieldWrite("promotion", "cost_snapshot"), RecallEditable("submit"))
    )
    _recall_info[_stage] = Cell(read=(_READ,))
    _freight_payment[_stage] = Cell(read=(_READ_OR_FINANCE,))
_fill(_recall_start, "AB", Cell(grey=(_WRITE,), hint="尚未发货，直接取消即可"))

PROMOTION_MATRIX = Matrix(
    kind="promotion",
    stages=PROMOTION_STAGES,
    rows=(
        Row("ship_include", "action", _ship_include),
        Row("ship_push", "action", _ship_push),
        Row("ship_withdraw", "action", _ship_withdraw),
        Row("recall_start", "action", _recall_start),
        Row("recall_waybill", "action", _recall_waybill),
        Row("recall_result", "action", _recall_result),
        Row("freight_submit", "action", _freight_submit),
        Row("urge", "action", _urge),
        Row("urge_complete", "action", _urge),
        Row("comment", "action", _comment),
        Row("deactivate", "action", _deactivate),
        Row("review", "action", _review),
        Row("settlement_resubmit", "action", _settlement_resubmit),
        Row("settlement_chat", "action", _settlement_chat_action),
        Row("metrics", "action", _metrics_action),
        Row("retro_write", "action", _retro_write),
        Row("retro_revise", "action", _retro_revise),
        Row("goods", "field", _goods),
        Row("goods_items", "field", _goods_items),
        Row("cooperation", "field", _cooperation),
        Row("platform_posts", "field", _platform_posts),
        Row("cooperation_mode", "field", _cooperation_mode),
        Row("quote_amount", "field", _quote_amount),
        Row("receiver", "field", _receiver),
        Row("shipping", "field", _shipping),
        Row("timeline", "field", _timeline),
        Row("metrics", "field", _metrics),
        Row("retro", "field", _retro),
        Row("payment_qr", "field", _payment_qr),
        Row("settlement", "field", _settlement),
        Row("settlement_chat", "field", _settlement_chat),
        Row("recall_info", "field", _recall_info),
        Row("freight_payment", "field", _freight_payment),
    ),
    # 5.4 PATCH 字段归属：PR-2 入矩阵的收件三项与 sku_id，PR-5 入矩阵的归属商品、合作信息、合作模式、服务费与停用，
    # PR-6 入矩阵的点赞（7 天数据）；其余 PATCH 字段（note_title、source_extra 等）沿用现状。顺序 = 403 里 fields 的顺序
    patch_groups={
        "receiver_name": "receiver",
        "receiver_phone": "receiver",
        "receiver_address": "receiver",
        "sku_id": "goods_items",
        "goods_main_id": "goods",
        "platform": "cooperation",
        "content_category": "cooperation",
        "scheduled_publish_date": "cooperation",
        "remark": "cooperation",
        "cooperation_mode": "cooperation_mode",
        "quote_amount": "quote_amount",
        "like_count": "metrics",
        "is_active": "deactivate",
    },
)

# 仓库行用推广单同一套阶段（偏差 §2-5）；仓库页只有 B ~ H 的单
WAREHOUSE_MATRIX = Matrix(
    kind="warehouse",
    stages=PROMOTION_STAGES,
    rows=(Row("ship_fill", "action", _ship_fill),),
)

MATRICES.update({"promotion": PROMOTION_MATRIX, "warehouse": WAREHOUSE_MATRIX})

__all__ = [
    "METRICS_SETTLE_HINT",
    "PAYMENT_QR_GREY_HINT",
    "PROMOTION_MATRIX",
    "REVIEW_GATES",
    "REVIEW_READ_REASON",
    "SETTLEMENT_CHAT_GREY_HINT",
    "SETTLEMENT_GREY_HINT",
    "SETTLEMENT_RESUBMIT_GATES",
    "SHIPPING_GREY_HINT",
    "SHIP_PUSH_GATES",
    "SHIP_PUSH_REASON",
    "WAREHOUSE_MATRIX",
    "metrics_wait_hint",
]
