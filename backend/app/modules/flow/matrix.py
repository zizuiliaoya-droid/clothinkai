"""流程线能力矩阵：一张表、三个出口（流程线设计 5.4、7.1、7.8）。

纯 Python、不碰库：service 把单据组装成轻量快照（``FlowDocBase`` 的 frozen 子类），矩阵只读快照与当前用户的权限。

表的形状
- 每行 ``Row(key, kind, cells)``：``kind`` 是 ``"action"``（动作键）或 ``"field"``（字段分组键），键名见下方常量；
  ``cells`` 的键是**精确阶段**（推广单用 3.8 的阶段名、谈款用状态），值是一个或几个 ``Cell``——同一阶段还要按子状态区分时，
  用 ``when=(SettlementIn(...),)`` 这类谓词收窄，取第一个命中的格；都不命中 = 隐
- ``Cell`` 依次判 ``edit`` → 改、``read`` → 读、``grey`` → 灰，都不成立 → 隐。每级是一组判断的「且」；
  ``None`` = 这一级没人，空元组 = 所有人
- 判断分两类：能力项（``Scope``、``FieldWrite``，不满足 = 缺权限）与规则项（``Owner``、``NotPrOwner``、``NoStar``，
  不满足 = ``FLOW_ACTION_FORBIDDEN``，带 ``rule`` 与 ``reason``）。``AnyOf`` 是「或」，两类都能组合：
  设计按角色列写的格子（「PR 本人改 / 主管改 / 管理员改」）写成 ``AnyOf(Owner(), Scope("promotion.review", "approve"))``；
  全部不满足时按第一个规则项归类（没有规则项 = 缺权限）
- 多角色逐格取最宽：``perms`` 本来就是各角色权限的并集，判断对并集做，自然落在能达到的最高一级。
  持 ``*`` 的 ``has()`` 恒真，等同管理员；``NoStar()`` 让他在那一格落到下一级（通常是「读」）

状态机不归矩阵：每种单据自己实现 ``allowed_actions()``（当前精确状态允许哪些动作），矩阵只管「谁改 / 读 / 灰 / 隐」。

三个出口
1. ``ui_for(actor, doc)`` → ``UiState``（``to_dict(view=...)`` 出 7.1 的 ``ui`` 形状）
2. ``require(actor, doc, action)``：状态机 422 → 规则 403 ``FLOW_ACTION_FORBIDDEN`` → ★ 缺项 422 ``FLOW_GATE_MISSING``；
   端点 scope 已在路由层判过，这里不重复判
3. ``writable_fields(actor, doc)`` / ``ensure_patch_allowed(...)``：过渡期只管已入矩阵的 PATCH 字段（``Matrix.patch_groups``），
   没入矩阵的沿用现状

PR-1 只有类型与三个出口；PR-2 起各单据模块在 import 时把自己的表 ``MATRICES.update(...)`` 进来
（推广单 / 仓库行：``promotion/flow_matrix.py``，由 ``promotion/flow_doc.py`` import 保证注册）。
改矩阵必须同时改设计 5.2 / 5.3 与 ``tests/unit/fixtures/flow_matrix_expected.py``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, ClassVar, Literal
from uuid import UUID

from app.core.exceptions import (
    FieldPermissionDenied,
    IllegalStateTransitionError,
    PermissionDeniedError,
)
from app.core.security.field_permissions import (
    FieldPermissionContext,
    can_read_field,
    can_write_field,
)
from app.core.security.permissions import EffectivePermissions
from app.modules.flow.exceptions import FlowActionForbiddenError, FlowGateMissingError

# ---------------------------------------------------------------------------
# 键（7.1，前后端契约；前端 features/flow/keys.ts 照抄）。顺序 = 前端菜单顺序
# ---------------------------------------------------------------------------

KINDS: tuple[str, ...] = ("negotiation", "promotion", "warehouse", "settlement", "freight")

ACTION_KEYS: dict[str, tuple[str, ...]] = {
    "negotiation": (
        "edit",
        "submit",
        "manager_review",
        "final_review",
        "receiver_save",
        "receiver_confirm",
        "copy",
        "comment",
    ),
    "promotion": (
        "ship_include",
        "ship_push",
        "ship_withdraw",
        "publish",
        "cancel",
        "recall_start",
        "recall_waybill",
        "recall_result",
        "urge",
        "urge_complete",
        "review",
        "resubmit",
        "settlement_resubmit",
        "settlement_chat",
        "metrics",
        "retro_write",
        "retro_revise",
        "freight_submit",
        "comment",
        "deactivate",
    ),
    "warehouse": ("ship_fill",),
    "settlement": ("legacy_approve", "reject", "extra_item", "fill_payment", "upload_proof"),
    "freight": ("freight_review", "freight_pay", "freight_reject_payment", "freight_resubmit"),
}

# 列表响应的页级动作（新建、导入、导出）；PR-1 只定常量，页级 ui 随各页面的 PR 接
PAGE_ACTION_KEYS: dict[str, tuple[str, ...]] = {
    "negotiation": ("create",),
    "promotion": ("create", "import"),
    "warehouse": ("export",),
    "settlement": ("import",),
    "freight": (),
}

FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "negotiation": (
        "basic",
        "quote_amount",
        "manager_opinion",
        "final_opinion",
        "receiver",
        "timeline",
        "overdue_badge",
        "others_in_progress",
    ),
    "promotion": (
        "goods",
        "goods_items",
        "cooperation",
        "cooperation_mode",
        "quote_amount",
        "receiver",
        "shipping",
        "publish_info",
        "platform_posts",
        "metrics",
        "payment_qr",
        "return_waybill",
        "recall_info",
        "review_result",
        "settlement",
        "settlement_chat",
        "retro",
        "freight_payment",
        "other_info",
        "timeline",
    ),
    "warehouse": (),
    "settlement": (),
    "freight": (),
}

ActionState = Literal["enabled", "disabled", "grey"]
FieldState = Literal["edit", "read", "grey"]
RowKind = Literal["action", "field"]
View = Literal["list", "detail"]


class Level(IntEnum):
    """一格的取值。数值越大越宽。"""

    HIDDEN = 0
    GREY = 1
    READ = 2
    EDIT = 3

    @property
    def label(self) -> str:
        return _LEVEL_LABELS[self]


_LEVEL_LABELS = {Level.HIDDEN: "隐", Level.GREY: "灰", Level.READ: "读", Level.EDIT: "改"}


# ---------------------------------------------------------------------------
# 当前用户与单据快照
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlowActor:
    """矩阵眼里的当前用户：有效权限（各角色并集，已扣掉撤销）+ 字段权限上下文。"""

    user_id: UUID
    perms: EffectivePermissions
    field_ctx: FieldPermissionContext

    @property
    def is_star(self) -> bool:
        return "*" in self.perms.scopes


@dataclass(frozen=True, kw_only=True)
class FlowDocBase:
    """单据快照基类。各单据（PromotionDoc / NegotiationDoc / SettlementDoc / FreightDoc）随各自的 PR 定义。

    - ``kind``：类属性，``KINDS`` 之一，决定用哪张矩阵
    - ``stage``：``cells`` 的键（推广单 = 3.8 的阶段名；谈款 / 结款单 / R33 = 状态值）
    - ``state``：状态机里的当前状态，非法转移时作 ``from_state`` 返回
    - ``column``：``ui.column``（推广单 = 5.3 的 A ~ H）
    - ``owner_id``：「本人」（推广单 = 负责 PR；谈款 = 发起人）；``negotiator_id``：谈款人（``NotPrOwner`` 用）。
      service 组快照时 ``negotiator_id`` 必须填：没有谈款的推广单（导入、主管补录）回落到 ``pr_id``——
      留空会让 ``NotPrOwner`` 恒真、自审规则静默放行
    """

    kind: ClassVar[str]
    stage: str
    state: str
    column: str | None = None
    owner_id: UUID | None = None
    negotiator_id: UUID | None = None

    def allowed_actions(self) -> frozenset[str]:
        """当前精确状态（含子状态）下状态机允许的动作键。子类必须实现。"""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# 格子里的判断
# ---------------------------------------------------------------------------


class Check(ABC):
    @abstractmethod
    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool: ...


class Capability(Check):
    """能力项：不满足 = 缺权限（``require`` 抛 ``PERMISSION_DENIED``）。"""


class Rule(Check):
    """规则项：不满足 = ``FLOW_ACTION_FORBIDDEN``，``reason`` 与按钮悬停文案同一份。"""

    rule: ClassVar[str]
    reason: str


@dataclass(frozen=True)
class Scope(Capability):
    scope: str
    action: str = "read"

    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool:
        return actor.perms.has(self.scope, self.action)


@dataclass(frozen=True)
class FieldWrite(Capability):
    entity: str
    field: str

    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool:
        return can_write_field(self.entity, self.field, actor.field_ctx)


@dataclass(frozen=True)
class FieldRead(Capability):
    """字段读规则（4.6）：字段级权限先于矩阵，读不到的字段所在分组不该出现在 ``ui``（7.1）。"""

    entity: str
    field: str

    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool:
        return can_read_field(self.entity, self.field, actor.field_ctx)


@dataclass(frozen=True)
class Star(Capability):
    """持 ``*``（管理员）。5.3 里「只有管理员改」的格（已推送后的收件、颜色尺码，已完结的发货信息）。"""

    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool:
        return actor.is_star


class AnyOf(Check):
    """几项满足一个即可（「或」），能力项、规则项都能组合，可以嵌套。

    全部不满足时由 ``blame`` 归类：第一个规则项（嵌套的 ``AnyOf`` 按它自己的 ``blame``）决定
    ``rule`` / ``reason``——ui 的「读」原因和 ``require`` 的 403 用同一份；一个规则项都没有 = 缺权限。
    """

    def __init__(self, *items: Check) -> None:
        if not items:
            raise ValueError("AnyOf 至少要有一项")
        for item in items:
            if not isinstance(item, Check):
                raise TypeError(f"AnyOf 只能组合判断项，收到 {item!r}")
        self.items = items

    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool:
        return any(item.ok(actor, doc) for item in self.items)

    @property
    def blame(self) -> Check:
        """整体不满足时归咎的那一项：第一个规则项；没有规则项就是自己（按能力项处理）。"""
        for item in self.items:
            culprit = _blame(item)
            if isinstance(culprit, Rule):
                return culprit
        return self

    def __repr__(self) -> str:
        return f"AnyOf{self.items!r}"


def _blame(check: Check) -> Check:
    return check.blame if isinstance(check, AnyOf) else check


@dataclass(frozen=True)
class Owner(Rule):
    """本人（``doc.owner_id``）。持 ``*`` 也不放行；「本人或主管 / 管理员」写成 ``AnyOf(Owner(), Scope(...))``。"""

    reason: str = "只有负责人本人可以操作"
    rule: ClassVar[str] = "not_owner"

    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool:
        return doc.owner_id is not None and doc.owner_id == actor.user_id


@dataclass(frozen=True)
class NotPrOwner(Rule):
    """≠ 谈款人：不能审自己谈的单。"""

    reason: str = "不能审核自己谈的单"
    rule: ClassVar[str] = "self_review"

    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool:
        return doc.negotiator_id != actor.user_id


@dataclass(frozen=True)
class NoStar(Rule):
    """不持 ``*``：谈款第一级审核管理员不参与（Q3）。"""

    reason: str = "管理员不参与这一级审核"
    rule: ClassVar[str] = "star_first_level"

    def ok(self, actor: FlowActor, doc: FlowDocBase) -> bool:
        return not actor.is_star


# ---------------------------------------------------------------------------
# 子状态谓词：同一阶段里再按子状态收窄。读快照属性时不给默认值——
# 谓词用在了没有这个属性的单据上是写错了，当场 AttributeError
# ---------------------------------------------------------------------------


class _AttrIn:
    attr: ClassVar[str]

    def __init__(self, *values: str | None) -> None:
        if not values:
            raise ValueError(f"{type(self).__name__} 至少要有一个取值")
        self.values = frozenset(values)

    def matches(self, doc: FlowDocBase) -> bool:
        return getattr(doc, self.attr) in self.values

    def __repr__(self) -> str:
        return f"{type(self).__name__}({sorted(map(str, self.values))!r})"


class StageIn(_AttrIn):
    attr = "stage"


class SettlementIn(_AttrIn):
    """结款单状态。"""

    attr = "settlement_status"


class FreightIn(_AttrIn):
    """R33 运费付款单状态。"""

    attr = "freight_status"


class RecallIn(_AttrIn):
    attr = "recall_status"


class PublishIn(_AttrIn):
    attr = "publish_status"


class ShipIn(_AttrIn):
    """推广单发货状态（3.3）；``ShipIn(None)`` = 历史单（发货为空）。"""

    attr = "ship_status"


Predicate = _AttrIn


# ---------------------------------------------------------------------------
# ★ 卡点、格、行、表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Gate:
    """★ 卡点。``in_dialog=True``：缺项能在这个动作自己的弹窗里补，按钮不禁用（7.1）。

    ``present`` 不给时按 ``bool(getattr(doc, key))`` 判（不给默认值：快照没有这个属性就是写错了）。
    """

    key: str
    label: str
    in_dialog: bool = False
    present: Callable[[Any], bool] | None = field(default=None, compare=False)

    def is_missing(self, doc: FlowDocBase) -> bool:
        if self.present is not None:
            return not self.present(doc)
        return not getattr(doc, self.key)


Hint = str | Callable[[Any], str]


@dataclass(frozen=True)
class Cell:
    edit: tuple[Check, ...] | None = None
    read: tuple[Check, ...] | None = None
    reason: str = ""  # 「读」的悬停原因；edit 里第一个不满足的是规则项时用规则项自己的 reason
    grey: tuple[Check, ...] | None = None
    hint: Hint = ""  # 「灰」的提示，可以按快照算（如可录日期）
    gates: tuple[Gate, ...] = ()
    when: tuple[Predicate, ...] = ()

    def hint_for(self, doc: FlowDocBase) -> str:
        return self.hint(doc) if callable(self.hint) else self.hint


@dataclass(frozen=True)
class Row:
    key: str
    kind: RowKind
    cells: Mapping[str, Cell | tuple[Cell, ...]]

    def candidates(self, stage: str) -> tuple[Cell, ...]:
        found = self.cells.get(stage)
        if found is None:
            return ()
        return (found,) if isinstance(found, Cell) else found

    def cell_for(self, doc: FlowDocBase) -> Cell | None:
        for cell in self.candidates(doc.stage):
            if all(p.matches(doc) for p in cell.when):
                return cell
        return None


@dataclass(frozen=True)
class Matrix:
    """一种单据的矩阵。``patch_groups``：PATCH 字段名 → 字段分组键（只登记已入矩阵的，5.4 过渡规则）。"""

    kind: str
    stages: tuple[str, ...]
    rows: tuple[Row, ...]
    patch_groups: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"未知单据类型: {self.kind!r}")
        if len(set(self.stages)) != len(self.stages):
            raise ValueError(f"{self.kind} 的阶段有重复")
        stages = set(self.stages)
        seen: set[tuple[str, str]] = set()
        for row in self.rows:
            known = ACTION_KEYS[self.kind] if row.kind == "action" else FIELD_KEYS[self.kind]
            if row.kind not in ("action", "field") or row.key not in known:
                raise ValueError(f"{self.kind} 没有这个{row.kind}键: {row.key!r}")
            if (row.kind, row.key) in seen:
                raise ValueError(f"{self.kind} 的行重复: {row.kind}:{row.key}")
            seen.add((row.kind, row.key))
            for stage in row.cells:
                if stage not in stages:
                    raise ValueError(f"{row.kind}:{row.key} 用了未登记的阶段 {stage!r}")
                for cell in row.candidates(stage):
                    if cell.grey is not None and not cell.hint:
                        raise ValueError(f"{row.kind}:{row.key}@{stage} 有灰格但没有提示")
                    if row.kind == "field" and cell.gates:
                        raise ValueError(f"字段行 {row.key} 不能挂 ★ 卡点")
        for patch_field, group in self.patch_groups.items():
            if ("field", group) not in seen:
                raise ValueError(f"PATCH 字段 {patch_field} 指向未入矩阵的分组 {group!r}")

    def row(self, kind: RowKind, key: str) -> Row | None:
        for row in self.rows:
            if row.kind == kind and row.key == key:
                return row
        return None

    def check_stage(self, stage: str) -> None:
        if stage not in self.stages:
            raise ValueError(f"{self.kind} 矩阵没有阶段 {stage!r}")


# 真实矩阵：PR-2 起各 PR 把它碰到的行加进来（设计 10.1），由各单据模块 import 时登记
MATRICES: dict[str, Matrix] = {}


def matrix_for(kind: str) -> Matrix:
    try:
        return MATRICES[kind]
    except KeyError:
        raise KeyError(f"流程线矩阵未登记单据类型 {kind!r}") from None


# ---------------------------------------------------------------------------
# 求值
# ---------------------------------------------------------------------------


def _first_failing(checks: tuple[Check, ...], actor: FlowActor, doc: FlowDocBase) -> Check | None:
    for check in checks:
        if not check.ok(actor, doc):
            return check
    return None


def _passes(checks: tuple[Check, ...] | None, actor: FlowActor, doc: FlowDocBase) -> bool:
    return checks is not None and _first_failing(checks, actor, doc) is None


def _evaluate(cell: Cell, actor: FlowActor, doc: FlowDocBase) -> tuple[Level, Check | None]:
    """返回 (这一格的级别, edit 里第一个不满足的判断；是 ``AnyOf`` 时换成它归咎的那一项)。"""
    failed = _first_failing(cell.edit, actor, doc) if cell.edit is not None else None
    failed = _blame(failed) if failed is not None else None
    if cell.edit is not None and failed is None:
        return Level.EDIT, None
    if _passes(cell.read, actor, doc):
        return Level.READ, failed
    if _passes(cell.grey, actor, doc):
        return Level.GREY, failed
    return Level.HIDDEN, failed


def cell_level(actor: FlowActor, doc: FlowDocBase, kind: RowKind, key: str) -> Level:
    """矩阵这一格的取值（不过状态机）。逐格比对期望表用。"""
    m = matrix_for(doc.kind)
    m.check_stage(doc.stage)
    row = m.row(kind, key)
    cell = row.cell_for(doc) if row is not None else None
    if cell is None:
        return Level.HIDDEN
    return _evaluate(cell, actor, doc)[0]


def _missing(cell: Cell, doc: FlowDocBase) -> tuple[Gate, ...]:
    return tuple(g for g in cell.gates if g.is_missing(doc))


def _read_reason(cell: Cell, failed: Check | None) -> str:
    return failed.reason if isinstance(failed, Rule) else cell.reason


def _missing_reason(missing: tuple[Gate, ...]) -> str:
    return "缺：" + "、".join(g.label for g in missing)


# ---------------------------------------------------------------------------
# 出口 1：ui
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UiAction:
    state: ActionState
    reason: str | None = None
    hint: str | None = None
    missing: tuple[tuple[str, str], ...] = ()  # (key, label)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"state": self.state}
        if self.reason is not None:
            out["reason"] = self.reason
        if self.hint is not None:
            out["hint"] = self.hint
        if self.missing:
            out["missing"] = [{"key": k, "label": label} for k, label in self.missing]
        return out


@dataclass(frozen=True)
class UiField:
    state: FieldState
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"state": self.state}
        if self.hint is not None:
            out["hint"] = self.hint
        return out


@dataclass(frozen=True)
class UiState:
    """``actions`` / ``fields`` 只含不是「隐」的键，按常量顺序排。"""

    column: str | None
    actions: Mapping[str, UiAction]
    fields: Mapping[str, UiField]

    @property
    def edits(self) -> tuple[str, ...]:
        return tuple(k for k, f in self.fields.items() if f.state == "edit")

    def to_dict(self, *, view: View) -> dict[str, Any]:
        """7.1 的形状：列表行 = actions + edits；详情 / 抽屉 = actions + fields。"""
        out: dict[str, Any] = {}
        if self.column is not None:
            out["column"] = self.column
        out["actions"] = {k: a.to_dict() for k, a in self.actions.items()}
        if view == "list":
            out["edits"] = list(self.edits)
        elif view == "detail":
            out["fields"] = {k: f.to_dict() for k, f in self.fields.items()}
        else:
            raise ValueError(f"未知视图: {view!r}")
        return out


def _gate_items(gates: tuple[Gate, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((g.key, g.label) for g in gates)


def _action_ui(row: Row, actor: FlowActor, doc: FlowDocBase, *, allowed: bool) -> UiAction | None:
    cell = row.cell_for(doc)
    if cell is None:
        return None
    if not allowed:
        # 状态机不允许：只有矩阵标了「灰」的给灰（「还没到」），其余隐
        if _passes(cell.grey, actor, doc):
            return UiAction("grey", hint=cell.hint_for(doc))
        return None
    level, failed = _evaluate(cell, actor, doc)
    if level is Level.EDIT:
        missing = _missing(cell, doc)
        if all(g.in_dialog for g in missing):
            return UiAction("enabled", missing=_gate_items(missing))
        return UiAction("disabled", reason=_missing_reason(missing), missing=_gate_items(missing))
    if level is Level.READ:
        return UiAction("disabled", reason=_read_reason(cell, failed))
    if level is Level.GREY:
        return UiAction("grey", hint=cell.hint_for(doc))
    return None


def _field_ui(row: Row, actor: FlowActor, doc: FlowDocBase) -> UiField | None:
    cell = row.cell_for(doc)
    if cell is None:
        return None
    level = _evaluate(cell, actor, doc)[0]
    if level is Level.EDIT:
        return UiField("edit")
    if level is Level.READ:
        return UiField("read")
    if level is Level.GREY:
        return UiField("grey", hint=cell.hint_for(doc))
    return None


def ui_for(actor: FlowActor, doc: FlowDocBase) -> UiState:
    """动作先过状态机（当前精确状态），再取格；字段不过状态机，直接取格。"""
    m = matrix_for(doc.kind)
    m.check_stage(doc.stage)
    allowed = doc.allowed_actions()
    actions: dict[str, UiAction] = {}
    for key in ACTION_KEYS[m.kind]:
        row = m.row("action", key)
        if row is None:
            continue
        ui = _action_ui(row, actor, doc, allowed=key in allowed)
        if ui is not None:
            actions[key] = ui
    fields: dict[str, UiField] = {}
    for key in FIELD_KEYS[m.kind]:
        row = m.row("field", key)
        if row is None:
            continue
        fui = _field_ui(row, actor, doc)
        if fui is not None:
            fields[key] = fui
    return UiState(column=doc.column, actions=actions, fields=fields)


# ---------------------------------------------------------------------------
# 出口 2：require
# ---------------------------------------------------------------------------


def _action_cell(doc: FlowDocBase, action: str) -> Cell | None:
    m = matrix_for(doc.kind)
    if action not in ACTION_KEYS[m.kind]:
        raise ValueError(f"{m.kind} 没有动作键 {action!r}")
    m.check_stage(doc.stage)
    row = m.row("action", action)
    return row.cell_for(doc) if row is not None else None


def require(actor: FlowActor, doc: FlowDocBase, action: str, *, gates: bool = True) -> None:
    """service 在每个转移前调用。顺序固定（7.1 ②~④）：

    状态机不允许 → 422 ``ILLEGAL_STATE_TRANSITION``；到不了「改」→ 第一个不满足的是规则项 403 ``FLOW_ACTION_FORBIDDEN``、
    是能力项（或这一格没有「改」）403 ``PERMISSION_DENIED``；★ 缺项（含弹窗里补的）→ 422 ``FLOW_GATE_MISSING``。

    ``gates=False``：只判 ②③，★ 留给 ``ensure_gates``——弹窗里补的缺项要先在条件 UPDATE 之后写进去、
    重组快照再判（推送仓库 S3 的 ③ 前后，细化 §3）。
    """
    cell = _action_cell(doc, action)
    if action not in doc.allowed_actions():
        raise IllegalStateTransitionError(
            f"当前状态「{doc.state}」不允许此操作",
            details={"from_state": doc.state, "action": action},
        )
    if cell is None:
        raise PermissionDeniedError(details={"action": action})
    level, failed = _evaluate(cell, actor, doc)
    if level is not Level.EDIT:
        if isinstance(failed, Rule):
            raise FlowActionForbiddenError(rule=failed.rule, reason=failed.reason)
        raise PermissionDeniedError(details={"action": action})
    if gates:
        _raise_missing(cell, doc)


def ensure_gates(actor: FlowActor, doc: FlowDocBase, action: str) -> None:
    """只判 ★（``require(..., gates=False)`` 的后半段）。

    ``doc`` 是补完弹窗内容后重组的快照，``stage`` 仍是动作之前的阶段（取同一格的卡点）；
    缺项 → 422 ``FLOW_GATE_MISSING``，``missing`` 与 ``ui`` 逐条相同。``actor`` 留着与 ``require`` 对称。
    """
    del actor
    cell = _action_cell(doc, action)
    if cell is None:
        raise PermissionDeniedError(details={"action": action})
    _raise_missing(cell, doc)


def _raise_missing(cell: Cell, doc: FlowDocBase) -> None:
    missing = _missing(cell, doc)
    if missing:
        raise FlowGateMissingError(_gate_items(missing))


# ---------------------------------------------------------------------------
# 出口 3：PATCH 字段
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WritableFields:
    managed: frozenset[str]  # 已入矩阵的 PATCH 字段
    allowed: frozenset[str]  # 其中当前用户此刻能写的


def writable_fields(actor: FlowActor, doc: FlowDocBase) -> WritableFields:
    m = matrix_for(doc.kind)
    m.check_stage(doc.stage)
    managed = frozenset(m.patch_groups)
    allowed = frozenset(
        f
        for f, group in m.patch_groups.items()
        if cell_level(actor, doc, "field", group) is Level.EDIT
    )
    return WritableFields(managed=managed, allowed=allowed)


def ensure_patch_allowed(actor: FlowActor, doc: FlowDocBase, fields_set: Iterable[str]) -> None:
    """过渡规则（5.4）：传入的字段里已入矩阵、但此刻写不了的，一次全列出来 403；没入矩阵的放行。

    顺序按 ``patch_groups`` 的登记顺序（与博主 / SKU 按声明顺序一致），``field`` = 其中第一个。
    """
    wf = writable_fields(actor, doc)
    requested = set(fields_set)
    denied = [
        f for f in matrix_for(doc.kind).patch_groups if f in requested and f not in wf.allowed
    ]
    if denied:
        raise FieldPermissionDenied(fields=denied, entity=doc.kind)
