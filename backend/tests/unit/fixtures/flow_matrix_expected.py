"""流程线矩阵的冻结期望表（流程线设计 5.4）。

**改矩阵（``app/modules/flow/matrix.py`` 的 ``MATRICES``）必须同步改设计 5.2 / 5.3 与本表。**

- ``EXPECTED[kind][(阶段标签, persona, 行类型, 行键)] = "改" | "读" | "灰" | "隐"``，与 ``MATRICES`` 逐格比对
  （``tests/unit/test_flow_matrix.py::test_real_matrices_match_expected``）
- ``DOCS[kind][阶段标签]`` 是比对用的单据快照：阶段标签 = 精确阶段名；有子状态谓词的格按谓词取值展开，写成「阶段·子状态」。
  推广单默认合作模式「寄拍」，每个阶段另展开一份「阶段·模式空」（PR-5 合作模式 ²³）；``_labels`` 默认两份都带。
  PR-6 起收款码按结款单 / 推广单结款状态 / 发布状态再分（⁹、计划 D9），``VARIANTS`` 里的阶段另展开「阶段·子状态」，
  ``_labels`` 默认带上（``variants=False`` 不带）
- persona：pr（本人，即快照的 owner_id / negotiator_id）、pr2（另一个 PR）、pr_manager、admin、finance、operations、warehouse，
  按 ``DEFAULT_ROLES`` 构造
- 行类型：``action`` / ``field``（同名键如 promotion 的 ``metrics`` 两种都有）

PR-2 起每个 PR 把它碰到的行加进来（设计 10.1）。本表手写、不从矩阵代码推导：值照设计 5.3 / 细化 §6 逐列抄，
角色顺序是设计的 PR / 主管 / 管理员 / 财务 / 运营 / 仓库（6 个字），展开时 pr2 = pr（PR 列不区分本人，偏差 §2-5）。

谈款（PR-3，设计 5.2）：角色顺序是 PR（本人）/ 主管 / 管理员 / 财务 / 运营（5 个字）；别的 PR 与仓库整张单据「—」，
展开时 pr2、warehouse 一律「隐」。另有「主管待审·主管自建」：谈款人是主管本人（11-24），那一格的第 1 个字是
pr 这个 persona——此时他不是本人，所以全是「隐」。

结款单（PR-6）：同谈款的 5 个字（PR / 主管 / 管理员 / 财务 / 运营），列 = 5 个结款状态；每个状态另展开「状态·主管提交」
（结款单的 pr_id 是主管本人，核查 / 驳回不能审自己提交的）。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.modules.finance.flow_doc import SettlementDoc
from app.modules.negotiation.flow_doc import NegotiationDoc
from app.modules.promotion.flow_doc import PromotionDoc, WarehouseDoc
from app.modules.promotion.freight_flow import freight_doc

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
MODE_EMPTY = "模式空"  # 每个阶段再展开一份合作模式为空的（「阶段·模式空」，历史导入的单）

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

# 各阶段的结款状态（PR-6）：推广单自己的（3.4 的 7 个值）与结款单的（没有结款单 = None）；没列到的阶段 = 未核查 / None
_STAGE_SETTLEMENT: dict[str, dict[str, Any]] = {
    "待主管审核": {"promotion_settlement_status": "待核查"},
    "推广驳回 · 待重提": {"promotion_settlement_status": "已驳回"},
    "待财务付款": {"promotion_settlement_status": "待付款", "settlement_status": "待付款"},
    "结款驳回 · 待重提": {"promotion_settlement_status": "结款驳回", "settlement_status": "已驳回"},
    "待传结款截图": {"promotion_settlement_status": "已付款", "settlement_status": "已付款"},
    **{
        stage: {"promotion_settlement_status": "已结款", "settlement_status": "已付款"}
        for stage in ("待满 7 天", "待录 7 天数据", "待复盘", "已完结")
    },
}

# 子状态展开（PR-6，收款码 ⁹ / 计划 D9）：阶段 → {子状态标签: 快照覆盖}
VARIANTS: dict[str, dict[str, dict[str, Any]]] = {
    # ②：财务已填实付（结款单待财务付款）时锁码
    "待财务付款": {"结款单待财务付款": {"settlement_status": "待财务付款"}},
    # 已发布后召回：③ 已付款 / 已结款锁码（召回中例外随 PR-8）、② 结款单待财务付款锁码
    "召回中": {
        "已付款": {
            "publish_status": "已发布",
            "promotion_settlement_status": "已付款",
            "settlement_status": "已付款",
        },
        "已结款": {
            "publish_status": "已发布",
            "promotion_settlement_status": "已结款",
            "settlement_status": "已付款",
        },
        "结款单待财务付款": {
            "publish_status": "已发布",
            "promotion_settlement_status": "待付款",
            "settlement_status": "待财务付款",
        },
    },
    # ①：没发布过的单停用了，管理员也传不了码
    "已停用": {"未发布": {"publish_status": "未发布", "recall_status": "未召回"}},
}


def _v(stage: str, variant: str) -> str:
    assert variant in VARIANTS[stage], (stage, variant)
    return f"{stage}·{variant}"


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
        "retro_status": "待复盘" if column in "DEF" else "已完成",
        "cooperation_mode": "寄拍",
        "promotion_settlement_status": "未核查",
        "settlement_status": None,
        "quote_amount": Decimal("500.00"),
        "has_payment_qr": True,
        "return_waybill": "SF1234567890",
        "has_recall_waybill": False,
        "metrics_available_from": date(2026, 10, 27),
        **_COLUMN_STATE[column],
        **_STAGE_SETTLEMENT.get(stage, {}),
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


def _stage_labels(
    stage: str, *, history: bool, mode_empty: bool | None, variants: bool
) -> list[str]:
    if history:
        return [f"{stage}·{HISTORY}"]
    out: list[str] = []
    if mode_empty is not True:
        out.append(stage)
        if variants:
            out.extend(f"{stage}·{v}" for v in VARIANTS.get(stage, {}))
    if mode_empty is not False:
        out.append(f"{stage}·{MODE_EMPTY}")
    return out


def _labels(
    columns: str,
    *,
    history: bool = False,
    mode_empty: bool | None = None,
    variants: bool = True,
) -> list[str]:
    """``history``：只取「阶段·历史单」。否则 ``mode_empty`` 为 None 取有模式与「阶段·模式空」两份，
    True 只取模式空，False 只取有模式（合作模式那一行要分开写）；有模式的那份默认带上子状态展开（``VARIANTS``）。"""
    return [
        label
        for col in columns
        for stage in COLUMNS[col]
        for label in _stage_labels(stage, history=history, mode_empty=mode_empty, variants=variants)
    ]


def _at(*stages: str) -> list[str]:
    """这几个精确阶段的有模式、模式空两份，C ~ F 列再加历史单；不含子状态展开（按格另列）。"""
    out: list[str] = []
    for stage in stages:
        out += _stage_labels(stage, history=False, mode_empty=None, variants=False)
        if any(stage in COLUMNS[c] for c in "CDEF"):
            out.append(f"{stage}·{HISTORY}")
    return out


_PROMOTION_DOCS: dict[str, Any] = {}
for _col, _stages in COLUMNS.items():
    for _stage in _stages:
        _PROMOTION_DOCS[_stage] = _promotion_doc(_stage, _col)
        for _variant, _override in VARIANTS.get(_stage, {}).items():
            _PROMOTION_DOCS[f"{_stage}·{_variant}"] = _promotion_doc(_stage, _col, **_override)
        _PROMOTION_DOCS[f"{_stage}·{MODE_EMPTY}"] = _promotion_doc(
            _stage, _col, cooperation_mode=None
        )
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

# 结款单 5 态（与 SettlementStatus 对照着抄，不 import）；PR-6 只有动作行（结款页按 ui.actions 渲染，8.6）
SETTLEMENT_STATES: tuple[str, ...] = ("待核查", "待付款", "待财务付款", "已付款", "已驳回")
MANAGER_SUBMITTED = "·主管提交"  # 结款单的 pr_id 是主管本人（驳回 / 核查不能审自己提交的）


def _settlement_doc(state: str, **override: Any) -> SettlementDoc:
    kw: dict[str, Any] = {
        "stage": state,
        "state": state,
        "owner_id": PR_ID,
        "negotiator_id": PR_ID,
    }
    kw.update(override)
    return SettlementDoc(**kw)


_SETTLEMENT_DOCS: dict[str, Any] = {s: _settlement_doc(s) for s in SETTLEMENT_STATES}
for _state in SETTLEMENT_STATES:
    _SETTLEMENT_DOCS[f"{_state}{MANAGER_SUBMITTED}"] = _settlement_doc(
        _state, owner_id=MANAGER_ID, negotiator_id=MANAGER_ID
    )

DOCS: dict[str, dict[str, Any]] = {
    "promotion": _PROMOTION_DOCS,
    "warehouse": _WAREHOUSE_DOCS,
    "negotiation": _NEGOTIATION_DOCS,
    "settlement": _SETTLEMENT_DOCS,
    "freight": {
        **{state: freight_doc(state, PR_ID) for state in ("待审核", "待付款", "已付款", "已驳回")},
        "待审核·主管自谈": freight_doc("待审核", MANAGER_ID),
        "待审核·管理员自谈": freight_doc("待审核", UUID(int=4)),
    },
}


# ---------------------------------------------------------------------------
# 期望值：(行类型, 行键) → [(阶段标签们, 6 个字)]；没列到的格全是「隐」
# ---------------------------------------------------------------------------

_PROMOTION_ROWS: dict[tuple[str, str], list[tuple[list[str], str]]] = {
    # PR8专用动作逐格能力；具体状态是否显示仍由allowed_actions判。
    ("action", "recall_start"): [
        (_labels("AB"), "灰灰灰隐隐隐"),
        (_labels("CDEFGH") + _labels("CDEF", history=True), "改改改隐隐隐"),
    ],
    ("action", "recall_waybill"): [
        (_labels("ABCDEFGH") + _labels("CDEF", history=True), "改改改隐隐隐")
    ],
    ("action", "recall_result"): [
        (_labels("ABCDEFGH") + _labels("CDEF", history=True), "改改改隐隐隐")
    ],
    ("action", "freight_submit"): [
        (_labels("ABCDEFGH") + _labels("CDEF", history=True), "改改改隐隐隐")
    ],
    ("field", "recall_info"): [
        (_labels("ABCDEFGH") + _labels("CDEF", history=True), "读读读隐读隐")
    ],
    ("field", "freight_payment"): [
        (_labels("ABCDEFGH") + _labels("CDEF", history=True), "读读读读读隐")
    ],
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
    # —— PR-5 ——
    # 商品归属（¹⁹）：PR 读（合作信息由谈款带过来，11-53）；历史单按 A 列；H 只有管理员改
    ("field", "goods"): [
        (_labels("A") + _labels("CDEF", history=True), "读改改隐读隐"),
        (_labels("BCD"), "读改改隐读读"),
        (_labels("EFG"), "读改改读读读"),
        (_labels("H"), "读读改读读读"),
    ],
    # 合作信息（平台、约定发布时间、备注）：PR 读（11-16）；E 列起只有管理员改、财务读
    ("field", "cooperation"): [
        (_labels("ABCD") + _labels("CD", history=True), "读改改隐读隐"),
        (_labels("EFGH") + _labels("EF", history=True), "读读改读读隐"),
    ],
    ("field", "platform_posts"): [
        (_labels("ABC") + _labels("C", history=True), "灰灰灰隐灰隐"),
        (_labels("DEF") + _labels("DEF", history=True), "改改改隐读隐"),
        (_labels("G", variants=False), "隐隐隐隐隐隐"),
        ([_v("召回中", v) for v in VARIANTS["召回中"]], "改改改隐读隐"),
        (_labels("H"), "读读改隐读隐"),
    ],
    # 合作模式（²³）：有值所有人读；空值主管 / 管理员可补一次，H 只有管理员
    ("field", "cooperation_mode"): [
        (_labels("ABCD", mode_empty=False) + _labels("CD", history=True), "读读读隐读隐"),
        (_labels("EFGH", mode_empty=False) + _labels("EF", history=True), "读读读读读隐"),
        (_labels("ABCD", mode_empty=True), "读改改隐读隐"),
        (_labels("EFG", mode_empty=True), "读改改读读隐"),
        (_labels("H", mode_empty=True), "读读改读读隐"),
    ],
    # 服务费：运营过不了字段规则；结款单生成后（E 列起）所有人只读；G 列财务「隐」
    ("field", "quote_amount"): [
        (_labels("ABCD") + _labels("CD", history=True), "读改改隐隐隐"),
        (_labels("EFH") + _labels("EF", history=True), "读读读读隐隐"),
        (_labels("G"), "读读读隐隐隐"),
    ],
    # 催发：手动催、标记完成
    ("action", "urge"): [(_labels("ABC") + _labels("C", history=True), "改改改隐隐隐")],
    ("action", "urge_complete"): [(_labels("ABC") + _labels("C", history=True), "改改改隐隐隐")],
    # 停用推广单（11-15）：结款单生成后（E、F）与 H 只有管理员
    ("action", "deactivate"): [
        (_labels("ABCDG") + _labels("CD", history=True), "隐改改隐隐隐"),
        (_labels("EFH") + _labels("EF", history=True), "隐隐改隐隐隐"),
    ],
    # PR-7：复盘与结款并行；状态机收窄动作，H完成版只有*改。
    ("field", "retro"): [
        (_labels("DEF") + _labels("DEF", history=True), "读改改隐读隐"),
        (_labels("H"), "读读改隐读隐"),
        ([_v("召回中", v) for v in ("已付款", "已结款", "结款单待财务付款")], "读读改隐读隐"),
    ],
    ("action", "retro_write"): [
        (_labels("DEF") + _labels("DEF", history=True), "读改改隐隐隐"),
        ([_v("召回中", v) for v in ("已付款", "已结款", "结款单待财务付款")], "读改改隐隐隐"),
    ],
    ("action", "retro_revise"): [
        (_labels("DEFH") + _labels("DEF", history=True), "隐隐改隐隐隐"),
        ([_v("召回中", v) for v in ("已付款", "已结款", "结款单待财务付款")], "隐隐改隐隐隐"),
    ],
    # —— PR-6 ——
    # 推广审核（★¹⁵）：PR 读「需 PR 主管审核」；推广驳回时由状态机藏
    ("action", "review"): [(_labels("D") + _labels("D", history=True), "读改改隐隐隐")],
    # 结款重提（★¹⁷）、上传结款聊天截图（★¹³）：E 列；哪个阶段能点由状态机判
    ("action", "settlement_resubmit"): [
        (_labels("E") + _labels("E", history=True), "改改改隐隐隐")
    ],
    ("action", "settlement_chat"): [(_labels("E") + _labels("E", history=True), "改改改隐隐隐")],
    # 录 7 天数据（★⁸）：D、E 列灰「已结款后可录」；F 列改（待满 7 天灰、待复盘隐由状态机判）
    ("action", "metrics"): [
        (_labels("DE") + _labels("DE", history=True), "灰灰灰隐隐隐"),
        (_labels("F") + _labels("F", history=True), "改改改隐隐隐"),
    ],
    # 7 天数据字段（D13）：没结款、没满 7 天灰；待录 PR / 主管 / 管理员改；录过之后只有管理员改
    ("field", "metrics"): [
        (_labels("ABCDE") + _labels("CDE", history=True) + _at("待满 7 天"), "灰灰灰隐灰隐"),
        (_at("待录 7 天数据"), "改改改隐读隐"),
        (_at("待复盘") + _labels("H"), "读读改隐读隐"),
    ],
    # 收款码（⁹ + D9）：A ~ C 灰「发布后上传」；D 改；E 待财务付款按结款单拆（待财务付款锁）、结款驳回改、已付款锁；
    # F、H 锁（管理员改）；G 按推广单结款状态拆（已付款 / 已结款锁）、结款单待财务付款也锁；没发布过的停用单谁都只读
    ("field", "payment_qr"): [
        (_labels("ABC") + _labels("C", history=True), "灰灰灰隐隐隐"),
        (_labels("D") + _labels("D", history=True), "改改改隐隐隐"),
        (_at("待财务付款", "结款驳回 · 待重提"), "改改改读隐隐"),
        ([_v("待财务付款", "结款单待财务付款"), *_at("待传结款截图")], "读读改读隐隐"),
        (_labels("F") + _labels("F", history=True), "读读改读隐隐"),
        (_labels("G", variants=False), "改改改读隐隐"),
        ([_v("召回中", v) for v in ("已付款", "已结款")], "改改改读隐隐"),
        ([_v("召回中", "结款单待财务付款")], "读读改读隐隐"),
        (_labels("H", variants=False), "读读改读隐隐"),
        ([_v("已停用", "未发布")], "读读读读隐隐"),
    ],
    # 结款单（¹²）：D 灰（审核通过后生成）；待财务付款 主管 / 管理员 / 财务改；驳回、付款之后所有人只读
    ("field", "settlement"): [
        (_labels("D") + _labels("D", history=True), "灰灰灰隐隐隐"),
        ([*_at("待财务付款"), _v("待财务付款", "结款单待财务付款")], "读改改改隐隐"),
        (
            _at("结款驳回 · 待重提", "待传结款截图") + _labels("FH") + _labels("F", history=True),
            "读读读读隐隐",
        ),
    ],
    # 结款聊天截图（¹³）：财务付款前灰「财务付款后上传」；待传结款截图改；之后只有管理员改
    ("field", "settlement_chat"): [
        (
            [*_at("待财务付款", "结款驳回 · 待重提"), _v("待财务付款", "结款单待财务付款")],
            "灰灰灰灰隐隐",
        ),
        (_at("待传结款截图"), "改改改读隐隐"),
        (_labels("FH") + _labels("F", history=True), "读读改读隐隐"),
    ],
}

_WAREHOUSE_ROWS: dict[tuple[str, str], list[tuple[list[str], str]]] = {
    # 仓库行快照没有合作模式与结款状态，不展开「模式空」与子状态
    ("action", "ship_fill"): [
        (_labels("BCDEFG", mode_empty=False, variants=False), "隐隐改隐隐改"),
        (_labels("H", mode_empty=False, variants=False), "隐隐改隐隐隐"),
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


# 结款单（PR-6，5.3「结款单：追加项、填实付、驳回、上传凭证」隐/改¹⁶/改/改★¹²/隐/隐 按状态展开）：
# 5 个字 PR/主管/管理员/财务/运营；PR、运营没有 settlement scope（进不了结款页），别的 PR、仓库同样「—」。
# 核查 / 驳回挂 settlement.review:approve（主管、财务、管理员），结款单是自己提交的（pr_id = 本人）→「读」；
# 追加项 / 填实付挂 settlement:write + settlement.payment_amount 字段写（主管、财务、管理员）；
# 上传凭证挂 settlement.pay:upload_proof + finance.settlement:pay（财务、管理员；主管不能，¹⁶）
_SETTLE_EDIT = "隐改改改隐"
_SETTLE_SELF = "隐读改改隐"
_SETTLE_ROWS: dict[tuple[str, str], dict[str, str]] = {
    ("action", "legacy_approve"): {
        "待核查": _SETTLE_EDIT,
        f"待核查{MANAGER_SUBMITTED}": _SETTLE_SELF,
    },
    ("action", "reject"): {
        "待付款": _SETTLE_EDIT,
        "待财务付款": _SETTLE_EDIT,
        f"待付款{MANAGER_SUBMITTED}": _SETTLE_SELF,
        f"待财务付款{MANAGER_SUBMITTED}": _SETTLE_SELF,
    },
    ("action", "extra_item"): {"待付款": _SETTLE_EDIT, f"待付款{MANAGER_SUBMITTED}": _SETTLE_EDIT},
    ("action", "fill_payment"): {
        "待付款": _SETTLE_EDIT,
        f"待付款{MANAGER_SUBMITTED}": _SETTLE_EDIT,
    },
    ("action", "upload_proof"): {
        "待财务付款": "隐隐改改隐",
        f"待财务付款{MANAGER_SUBMITTED}": "隐隐改改隐",
    },
}

EXPECTED: dict[str, dict[tuple[str, str, str, str], str]] = {
    "promotion": _expand(_PROMOTION_DOCS, _PROMOTION_ROWS),
    "warehouse": _expand(_WAREHOUSE_DOCS, _WAREHOUSE_ROWS),
    "negotiation": _expand_negotiation(_NEGOTIATION_DOCS, _NEG_ROWS),
    "settlement": _expand_negotiation(_SETTLEMENT_DOCS, _SETTLE_ROWS),
    "freight": _expand(
        DOCS["freight"],
        {
            ("action", "freight_review"): [
                (["待审核"], "隐改改隐隐隐"),
                (["待审核·主管自谈"], "隐读改隐隐隐"),
                (["待审核·管理员自谈"], "隐改读隐隐隐"),
            ],
            ("action", "freight_pay"): [(["待付款"], "隐隐改改隐隐")],
            ("action", "freight_reject_payment"): [(["待付款"], "隐隐改改隐隐")],
            ("action", "freight_resubmit"): [(["已驳回"], "改改改隐隐隐")],
        },
    ),
}
