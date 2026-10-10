"""推广单 / 仓库行的能力矩阵（流程线 5.3；PR-2 只有收件、颜色尺码明细、发货信息三类字段行与
推送 / 撤回 / 纳入发货 / 回填四个动作行）。

import 本模块即把 ``MATRICES["promotion"]`` 与 ``MATRICES["warehouse"]`` 登记进 ``flow.matrix``；
``promotion/flow_doc.py`` import 它，所以用到快照的地方都已注册。

列（A ~ H）是文档写法，代码里按 ``stage_calculator.PROMOTION_STAGES`` 的 22 个阶段逐格展开（走不到的阶段按列填）。
角色列 PR / 主管 / 管理员 / 财务 / 运营 / 仓库 落到能力项上：
- PR、主管、管理员 都持 ``promotion:write``（PR 与主管靠 ``promotion.*:*``，管理员靠 ``*``）；运营只有 ``promotion.*:read``
- 主管 = ``promotion_ship:push``（PR 没有：推送仓库「需管理员或 PR 主管确认」）；仓库 = ``promotion_ship:fill``，没有 ``promotion:read``（060 收回）
- 财务能读的格用 ``finance.settlement:read``；「只有管理员改」用 ``Star()``
- 收件三项先过字段规则（4.6）：读要 ``FieldRead``、改要 ``FieldWrite``（三项任一即可，逐项的写权限由 service 再判）

改这里必须同时改设计 5.3 与 ``tests/unit/fixtures/flow_matrix_expected.py``。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.modules.flow.matrix import (
    MATRICES,
    AnyOf,
    Cell,
    FieldRead,
    FieldWrite,
    Gate,
    Matrix,
    Row,
    Scope,
    ShipIn,
    Star,
)
from app.modules.promotion.exceptions import InvalidReceiverPhoneError
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
_STAR = Star()

_RECEIVER_READ = AnyOf(*(FieldRead("promotion", f) for f in RECEIVER_FIELDS))
_RECEIVER_WRITE = AnyOf(*(FieldWrite("promotion", f) for f in RECEIVER_FIELDS))
# 「读」里有仓库的格：仓库没有 promotion:read，只在仓库页看推送过的单
_READ_OR_WAREHOUSE = AnyOf(_READ, _FILL)
_READ_OR_WAREHOUSE_OR_FINANCE = AnyOf(_READ, _FILL, _FINANCE_READ)

SHIP_PUSH_REASON = "需管理员或 PR 主管确认"
SHIPPING_GREY_HINT = "推送后由仓库回填"


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

PROMOTION_MATRIX = Matrix(
    kind="promotion",
    stages=PROMOTION_STAGES,
    rows=(
        Row("ship_include", "action", _ship_include),
        Row("ship_push", "action", _ship_push),
        Row("ship_withdraw", "action", _ship_withdraw),
        Row("goods_items", "field", _goods_items),
        Row("receiver", "field", _receiver),
        Row("shipping", "field", _shipping),
    ),
    # 5.4 PATCH 字段归属：PR-2 入矩阵的只有收件三项与 sku_id，其余 PATCH 字段沿用现状
    patch_groups={
        "receiver_name": "receiver",
        "receiver_phone": "receiver",
        "receiver_address": "receiver",
        "sku_id": "goods_items",
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
    "PROMOTION_MATRIX",
    "SHIPPING_GREY_HINT",
    "SHIP_PUSH_GATES",
    "SHIP_PUSH_REASON",
    "WAREHOUSE_MATRIX",
]
