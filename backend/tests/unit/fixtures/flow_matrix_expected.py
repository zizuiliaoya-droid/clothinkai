"""流程线矩阵的冻结期望表（流程线设计 5.4）。

**改矩阵（``app/modules/flow/matrix.py`` 的 ``MATRICES``）必须同步改设计 5.2 / 5.3 与本表。**

- ``EXPECTED[kind][(阶段标签, persona, 行类型, 行键)] = "改" | "读" | "灰" | "隐"``，与 ``MATRICES`` 逐格比对
  （``tests/unit/test_flow_matrix.py::test_real_matrices_match_expected``）
- ``DOCS[kind][阶段标签]`` 是比对用的单据快照：阶段标签 = 精确阶段名；有子状态谓词的格按谓词取值展开，写成「阶段·子状态」
- persona：pr（本人，即快照的 owner_id / negotiator_id）、pr2（另一个 PR）、pr_manager、admin、finance、operations、warehouse，
  按 ``DEFAULT_ROLES`` 构造
- 行类型：``action`` / ``field``（同名键如 promotion 的 ``metrics`` 两种都有）

PR-2 起每个 PR 把它碰到的行加进来（设计 10.1）。本表手写、不从矩阵代码推导：值照设计 5.3 / 细化 §6 逐列抄，
角色顺序是设计的 PR / 主管 / 管理员 / 财务 / 运营 / 仓库（6 个字），展开时 pr2 = pr（PR 列不区分本人，偏差 §2-5）。

谈款（PR-3，设计 5.2）：角色顺序是 PR（本人）/ 主管 / 管理员 / 财务 / 运营（5 个字）；别的 PR 与仓库整张单据「—」，
展开时 pr2、warehouse 一律「隐」。另有「主管待审·主管自建」：谈款人是主管本人（11-24），那一格的第 1 个字是
pr 这个 persona——此时他不是本人，所以全是「隐」。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.negotiation.flow_doc import NegotiationDoc
from app.modules.promotion.flow_doc import PromotionDoc, WarehouseDoc

PR_ID = UUID(int=1)
MANAGER_ID = UUID(int=3)  # 期望表里的主管（谈款的本轮主管审核人；「主管自建」的谈款人）
PERSONA_ORDER = ("pr", "pr2", "pr_manager", "admin", "finance", "operations", "warehouse")

# 3.8 的 22 个阶段按 5.3 的列（与 stage_calculator 的常量对照着抄，不 import，免得两边一起错）
COLUMNS: dict[str, tuple[str, ...]] = {
    "A": ("待推送仓库",),
    "B": ("待仓库发货",),
    "C": ("档期内", "催发", "重要催发", "超时", "未排期"),
    "D": ("待主管审核", "推广驳回 · 待重提"),
    "E": ("待财务付款", "结款驳回 · 待重提", "待传结款截图"),
    "F": ("待满 7 天", "待录 7 天数据", "待复盘"),
    "G": ("召回中",),
    "H": ("已完结", "已完结 · 召回", "已完结 · 不合作", "已停用", "已删除", "状态异常"),
}
HISTORY = "历史单"  # C ~ F 各阶段再展开一份发货为空的（「阶段·历史单」）

# 各列快照的状态取值（矩阵只看 stage 与谓词；这里给与阶段一致的值，方便读）
_COLUMN_STATE: dict[str, dict[str, Any]] = {
    "A": {"publish_status": "未发布", "recall_status": "未召回", "ship_status": "待发货"},
    "B": {"publish_status": "未发布", "recall_status": "未召回", "ship_status": "待打单"},
    "C": {"publish_status": "未发布", "recall_status": "未召回", "ship_status": "已发货"},
    "D": {"publish_status": "已发布", "recall_status": "未召回", "ship_status": "已发货"},
    "E": {"publish_status": "已发布", "recall_status": "未召回", "ship_status": "已发货"},
    "F": {"publish_status": "已发布", "recall_status": "未召回", "ship_status": "已发货"},
    "G": {"publish_status": "已取消", "recall_status": "召回中", "ship_status": "已发货"},
    "H": {"publish_status": "已取消", "recall_status": "召回成功", "ship_status": "已发货"},
}


def _promotion_doc(stage: str, column: str, **override: Any) -> PromotionDoc:
    kw: dict[str, Any] = {
        "stage": stage,
        "state": stage,
        "column": column,
        "owner_id": PR_ID,
        "negotiator_id": PR_ID,
        "is_active": True,
        "receiver_name": "张三",
        "receiver_phone": "13812345678",
        "receiver_address": "杭州市某路 1 号",
        "items_complete": True,
        **_COLUMN_STATE[column],
    }
    kw.update(override)
    return PromotionDoc(**kw)


def _warehouse_doc(stage: str, column: str) -> WarehouseDoc:
    return WarehouseDoc(
        stage=stage,
        state=stage,
        column=column,
        owner_id=PR_ID,
        negotiator_id=PR_ID,
        ship_status=_COLUMN_STATE[column]["ship_status"],
    )


def _labels(columns: str, *, history: bool = False) -> list[str]:
    out: list[str] = []
    for col in columns:
        for stage in COLUMNS[col]:
            out.append(f"{stage}·{HISTORY}" if history else stage)
    return out


_PROMOTION_DOCS: dict[str, Any] = {}
for _col, _stages in COLUMNS.items():
    for _stage in _stages:
        _PROMOTION_DOCS[_stage] = _promotion_doc(_stage, _col)
        if _col in "CDEF":
            _PROMOTION_DOCS[f"{_stage}·{HISTORY}"] = _promotion_doc(_stage, _col, ship_status=None)

_WAREHOUSE_DOCS: dict[str, Any] = {
    stage: _warehouse_doc(stage, col) for col in "BCDEFGH" for stage in COLUMNS[col]
}

# 谈款 7 态（5.2 的列序，与 NegotiationStatus 对照着抄，不 import）
NEGOTIATION_STATES: tuple[str, ...] = (
    "草稿",
    "主管待审",
    "审核驳回",
    "待老板审核",
    "老板退回",
    "待补收货地址",
    "已定稿",
)
SELF_BUILT = "主管待审·主管自建"
# 主管审过的状态：快照带本轮主管审核人
_MANAGER_REVIEWED = {"审核驳回", "待老板审核", "老板退回", "待补收货地址", "已定稿"}


def _negotiation_doc(state: str, **override: Any) -> NegotiationDoc:
    kw: dict[str, Any] = {
        "stage": state,
        "state": state,
        "column": state,
        "owner_id": PR_ID,
        "negotiator_id": PR_ID,
        "reviewed_by": MANAGER_ID if state in _MANAGER_REVIEWED else None,
        "blogger_id": UUID(int=11),
        "style_id": UUID(int=12),
        "items_complete": True,
        "cooperation_mode": "寄拍",
        "platform": "小红书",
        "scheduled_publish_date": date(2026, 10, 20),
        "quote_amount": Decimal("500.00"),
        "receiver_name": "张三",
        "receiver_phone": "13812345678",
        "receiver_address": "杭州市某路 1 号",
    }
    kw.update(override)
    return NegotiationDoc(**kw)


_NEGOTIATION_DOCS: dict[str, Any] = {s: _negotiation_doc(s) for s in NEGOTIATION_STATES}
_NEGOTIATION_DOCS[SELF_BUILT] = _negotiation_doc(
    "主管待审", owner_id=MANAGER_ID, negotiator_id=MANAGER_ID
)

DOCS: dict[str, dict[str, Any]] = {
    "promotion": _PROMOTION_DOCS,
    "warehouse": _WAREHOUSE_DOCS,
    "negotiation": _NEGOTIATION_DOCS,
}


# ---------------------------------------------------------------------------
# 期望值：(行类型, 行键) → [(阶段标签们, 6 个字)]；没列到的格全是「隐」
# ---------------------------------------------------------------------------

_PROMOTION_ROWS: dict[tuple[str, str], list[tuple[list[str], str]]] = {
    # 收件三项：历史单（发货为空）按 A 列
    ("field", "receiver"): [
        (_labels("A") + _labels("CDEF", history=True), "改改改隐隐隐"),
        (_labels("B"), "改改改隐隐读"),
        (_labels("CDEFGH"), "读读改隐隐读"),
    ],
    # 颜色尺码明细：A 列主管改★（推送弹窗）；财务 E 列起读；历史单按 A 列
    ("field", "goods_items"): [
        (_labels("A") + _labels("CDEF", history=True), "读改改隐读隐"),
        (_labels("BCD"), "读读改隐读读"),
        (_labels("EFGH"), "读读改读读读"),
    ],
    # 发货信息：A 灰「推送后由仓库回填」；历史单不按 A 列
    ("field", "shipping"): [
        (_labels("A"), "灰灰灰隐灰隐"),
        (_labels("BCDEFG") + _labels("CDEF", history=True), "读读改隐读改"),
        (_labels("H"), "读读改隐读读"),
    ],
    ("action", "ship_push"): [(_labels("A"), "读改改隐隐隐")],
    ("action", "ship_withdraw"): [(_labels("B"), "读改改隐隐隐")],
    # 纳入发货：格在 C 列（是不是历史单由状态机判，格不分）
    ("action", "ship_include"): [(_labels("C") + _labels("C", history=True), "隐改改隐隐隐")],
    # 时间线（PR-4）：A ~ D 财务「—」、E 列起财务读；仓库页不放时间线；历史单不分
    ("field", "timeline"): [
        (_labels("ABCD") + _labels("CD", history=True), "读读读隐读隐"),
        (_labels("EFGH") + _labels("EF", history=True), "读读读读读隐"),
    ],
    # 留言 / 贴图（PR-4）：全阶段 PR / 主管 / 管理员改，财务、运营、仓库不出现
    ("action", "comment"): [
        (_labels("ABCDEFGH") + _labels("CDEF", history=True), "改改改隐隐隐"),
    ],
}

_WAREHOUSE_ROWS: dict[tuple[str, str], list[tuple[list[str], str]]] = {
    ("action", "ship_fill"): [
        (_labels("BCDEFG"), "隐隐改隐隐改"),
        (_labels("H"), "隐隐改隐隐隐"),
    ],
}


def _expand(
    docs: dict[str, Any], rows: dict[tuple[str, str], list[tuple[list[str], str]]]
) -> dict[tuple[str, str, str, str], str]:
    out: dict[tuple[str, str, str, str], str] = {}
    for (kind, key), groups in rows.items():
        values: dict[str, str] = {}
        for labels, six in groups:
            assert len(six) == 6, six
            for label in labels:
                assert label in docs, label
                assert label not in values, (key, label)
                values[label] = six
        for label in docs:
            six = values.get(label, "隐" * 6)
            seven = six[0] + six  # pr2 = pr
            for persona, level in zip(PERSONA_ORDER, seven, strict=True):
                out[(label, persona, kind, key)] = level
    return out


# 谈款：(行类型, 行键) → {阶段标签: 5 个字 PR/主管/管理员/财务/运营}；没列到的格全是「隐」
_ALL_READ = "读读读读读"
_NEG_ROWS: dict[tuple[str, str], dict[str, str]] = {
    ("field", "basic"): {
        "草稿": "改隐隐隐隐",
        "主管待审": _ALL_READ,
        "审核驳回": _ALL_READ,
        "待老板审核": _ALL_READ,
        "老板退回": "改读读读读",
        "待补收货地址": _ALL_READ,
        "已定稿": _ALL_READ,
        SELF_BUILT: "隐读读读读",
    },
    # 运营看不到服务费（字段规则 promotion.quote_amount）
    ("field", "quote_amount"): {
        "草稿": "改隐隐隐隐",
        "主管待审": "读读读读隐",
        "审核驳回": "读读读读隐",
        "待老板审核": "读读读读隐",
        "老板退回": "改读读读隐",
        "待补收货地址": "读读读读隐",
        "已定稿": "读读读读隐",
        SELF_BUILT: "隐读读读隐",
    },
    ("field", "manager_opinion"): {
        "草稿": "灰隐隐隐隐",
        "主管待审": "读改读读读",
        "审核驳回": _ALL_READ,
        "待老板审核": _ALL_READ,
        "老板退回": _ALL_READ,
        "待补收货地址": _ALL_READ,
        "已定稿": _ALL_READ,
        SELF_BUILT: "隐读读读读",  # 主管不能审自己建的
    },
    ("field", "final_opinion"): {
        "草稿": "灰隐隐隐隐",
        "主管待审": "灰灰灰灰灰",
        "待老板审核": "读读改读读",
        "老板退回": _ALL_READ,
        "待补收货地址": _ALL_READ,
        "已定稿": _ALL_READ,
        SELF_BUILT: "隐灰灰灰灰",
    },
    # 收货三项：财务、运营过不了字段规则 promotion.receiver_*
    ("field", "receiver"): {
        "草稿": "灰隐隐隐隐",
        "主管待审": "灰灰灰隐隐",
        "待老板审核": "灰灰灰隐隐",
        "老板退回": "灰灰灰隐隐",
        "待补收货地址": "改改改隐隐",
        "已定稿": "读读读隐隐",
        SELF_BUILT: "隐灰灰隐隐",
    },
    # 时间线（PR-4）：草稿只有本人；其余看得到这张谈款的人都读
    ("field", "timeline"): {
        "草稿": "读隐隐隐隐",
        **dict.fromkeys(NEGOTIATION_STATES[1:], _ALL_READ),
        SELF_BUILT: "隐读读读读",
    },
    ("field", "overdue_badge"): {"主管待审": "读读读隐隐", SELF_BUILT: "隐读读隐隐"},
    ("field", "others_in_progress"): {"草稿": "读隐隐隐隐", "老板退回": "读隐隐隐隐"},
    ("action", "edit"): {"草稿": "改隐隐隐隐", "老板退回": "改读读隐隐"},
    ("action", "submit"): {"草稿": "改隐隐隐隐", "老板退回": "改读读隐隐"},
    # 管理员「读」（NoStar）；主管自建时主管「读」（不能审自己谈的单）
    ("action", "manager_review"): {"主管待审": "读改读隐隐", SELF_BUILT: "隐读读隐隐"},
    ("action", "final_review"): {"待老板审核": "读读改隐隐"},
    ("action", "receiver_save"): {"待补收货地址": "改改改隐隐"},
    ("action", "receiver_confirm"): {"待补收货地址": "改改改隐隐"},
    ("action", "copy"): {"审核驳回": "改隐隐隐隐"},
    # 留言 / 贴图（PR-4，N11）：草稿只有本人；其余 PR / 主管 / 管理员改，财务、运营读
    ("action", "comment"): {
        "草稿": "改隐隐隐隐",
        **dict.fromkeys(NEGOTIATION_STATES[1:], "改改改读读"),
        SELF_BUILT: "隐改改读读",
    },
}


def _expand_negotiation(
    docs: dict[str, Any], rows: dict[tuple[str, str], dict[str, str]]
) -> dict[tuple[str, str, str, str], str]:
    out: dict[tuple[str, str, str, str], str] = {}
    for (kind, key), values in rows.items():
        for label, five in values.items():
            assert len(five) == 5, (key, label, five)
            assert label in docs, label
        for label in docs:
            five = values.get(label, "隐" * 5)
            seven = five[0] + "隐" + five[1:] + "隐"  # pr2、warehouse 整张单据「—」
            for persona, level in zip(PERSONA_ORDER, seven, strict=True):
                out[(label, persona, kind, key)] = level
    return out


EXPECTED: dict[str, dict[tuple[str, str, str, str], str]] = {
    "promotion": _expand(_PROMOTION_DOCS, _PROMOTION_ROWS),
    "warehouse": _expand(_WAREHOUSE_DOCS, _WAREHOUSE_ROWS),
    "negotiation": _expand_negotiation(_NEGOTIATION_DOCS, _NEG_ROWS),
}
