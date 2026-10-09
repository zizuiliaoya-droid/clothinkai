"""流程线矩阵骨架（流程线设计 5.4、7.1；9.3 G 块「矩阵逐格」「字段写权限」的框架层）。

PR-1 真实矩阵为空，三个出口用本文件里的玩具矩阵 + 玩具单据验证（借推广单的键）：
精确状态先判、灰格、隐、in_dialog、取最宽、``*`` 与 ``NoStar``、子状态谓词、防死键、``require`` 顺序、PATCH 字段。
错误一律比 ``exc.code``，不只比异常类型。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from typing import Any, ClassVar
from uuid import UUID, uuid4

import pytest

from app.core.exceptions import AppException, FieldPermissionDenied
from app.core.security.field_permissions import FieldPermissionContext
from app.core.security.permissions import EffectivePermissions
from app.modules.auth.default_roles import DEFAULT_ROLES
from app.modules.flow import matrix as mx
from app.modules.flow.exceptions import (
    FLOW_RULES,
    FlowActionForbiddenError,
    FlowGateMissingError,
)
from app.modules.flow.matrix import (
    ACTION_KEYS,
    FIELD_KEYS,
    KINDS,
    MATRICES,
    PAGE_ACTION_KEYS,
    AnyOf,
    Cell,
    FieldWrite,
    FlowActor,
    FlowDocBase,
    Gate,
    Level,
    Matrix,
    NoStar,
    NotPrOwner,
    Owner,
    Row,
    Scope,
    SettlementIn,
    StageIn,
    cell_level,
    ensure_patch_allowed,
    require,
    ui_for,
    writable_fields,
)
from tests.unit.fixtures.flow_matrix_expected import DOCS, EXPECTED

# ---------------------------------------------------------------------------
# persona：按 DEFAULT_ROLES 构造（与迁移 seed 的角色权限同一份来源）
# ---------------------------------------------------------------------------

PR_ID = UUID(int=1)
_SPECS = {r.code: r for r in DEFAULT_ROLES}


def actor(*codes: str, user_id: UUID | None = None) -> FlowActor:
    scopes = frozenset(s for c in codes for s in _SPECS[c].permissions)
    uid = user_id or uuid4()
    return FlowActor(
        user_id=uid,
        perms=EffectivePermissions(user_id=str(uid), scopes=scopes),
        field_ctx=FieldPermissionContext(
            role_codes=frozenset(codes),
            grants=frozenset(),
            revokes=frozenset(),
            is_superuser="*" in scopes,
        ),
    )


PERSONAS: dict[str, FlowActor] = {
    "pr": actor("pr", user_id=PR_ID),
    "pr2": actor("pr"),
    "pr_manager": actor("pr_manager"),
    "admin": actor("admin"),
    "finance": actor("finance"),
    "operations": actor("operations"),
    "warehouse": actor("warehouse"),
}
PERSONA_ORDER = tuple(PERSONAS)

GridKey = tuple[str, str, str, str]  # (阶段标签, persona, 行类型, 行键)


def render_grid(
    matrix: Matrix, docs: Mapping[str, FlowDocBase], personas: Mapping[str, FlowActor]
) -> dict[GridKey, str]:
    """逐格算出矩阵取值（不过状态机）：阶段标签 × persona × 行。没有格的位置是「隐」。"""
    grid: dict[GridKey, str] = {}
    for label, doc in docs.items():
        for name, who in personas.items():
            for row in matrix.rows:
                grid[(label, name, row.kind, row.key)] = cell_level(
                    who, doc, row.kind, row.key
                ).label
    return grid


def dead_rows(grid: Mapping[GridKey, str]) -> list[str]:
    """防死键：处处是「隐」的行（``kind:key``）。"""
    alive: dict[str, bool] = {}
    for (_label, _persona, kind, key), level in grid.items():
        row_id = f"{kind}:{key}"
        alive[row_id] = alive.get(row_id, False) or level != Level.HIDDEN.label
    return sorted(r for r, ok in alive.items() if not ok)


# ---------------------------------------------------------------------------
# 玩具单据与玩具矩阵
# ---------------------------------------------------------------------------

TOY_ALLOWED: dict[str, frozenset[str]] = {
    "待推送": frozenset({"ship_push", "cancel", "comment"}),
    "待财务付款": frozenset({"comment"}),
    "结款驳回": frozenset({"settlement_resubmit", "comment"}),
    "已发布": frozenset({"recall_start", "urge", "review", "freight_submit", "metrics", "comment"}),
}


@dataclass(frozen=True, kw_only=True)
class ToyDoc(FlowDocBase):
    kind: ClassVar[str] = "promotion"
    settlement_status: str | None = None
    color_size: str | None = "黑色 / M"
    receiver_phone: str | None = "13800000000"
    brand_comment: str | None = "att-1"

    def allowed_actions(self) -> frozenset[str]:
        return TOY_ALLOWED[self.stage]


@dataclass(frozen=True, kw_only=True)
class BareDoc(FlowDocBase):
    """没有 settlement_status 属性：谓词用错单据类型时要当场炸。"""

    kind: ClassVar[str] = "promotion"

    def allowed_actions(self) -> frozenset[str]:
        return TOY_ALLOWED[self.stage]


def toy_doc(stage: str, **kw: Any) -> ToyDoc:
    base: dict[str, Any] = {
        "stage": stage,
        "state": stage,
        "owner_id": PR_ID,
        "negotiator_id": PR_ID,
    }
    base.update(kw)
    return ToyDoc(**base)


W = Scope("promotion", "write")
R = Scope("promotion", "read")
PAY = Scope("finance.settlement", "pay")
NEG_REVIEW = Scope("negotiation.review", "approve")
_OWNED = Cell(edit=(W, Owner()), read=(R,), reason="由负责 PR 操作")
# 「本人，或持审核权的人（主管、临时代理人；管理员靠 *）」：设计 N8 / N9、11-20 这类按角色列取「或」的格子
_OWNED_OR_REVIEWER = Cell(
    edit=(W, AnyOf(Owner(), NEG_REVIEW)), read=(R,), reason="由负责 PR 或主管操作"
)

TOY_MATRIX = Matrix(
    kind="promotion",
    stages=("待推送", "待财务付款", "结款驳回", "已发布"),
    rows=(
        Row(
            "ship_push",
            "action",
            {
                "待推送": replace(
                    _OWNED,
                    gates=(
                        Gate("color_size", "颜色尺码", in_dialog=True),
                        Gate("receiver_phone", "收件电话", in_dialog=True),
                    ),
                )
            },
        ),
        Row("cancel", "action", {"待推送": _OWNED, "已发布": _OWNED}),
        Row(
            "recall_start",
            "action",
            {"待推送": Cell(grey=(R,), hint="还没发货，直接取消即可"), "已发布": _OWNED},
        ),
        Row("urge", "action", {"已发布": _OWNED_OR_REVIEWER}),
        Row(
            "review",
            "action",
            {
                "已发布": Cell(
                    edit=(Scope("promotion.review", "approve"), NoStar(), NotPrOwner()),
                    read=(R,),
                    reason="需 PR 主管审核",
                    gates=(Gate("brand_comment", "品牌词评论截图"),),
                )
            },
        ),
        Row("settlement_resubmit", "action", {"待财务付款": _OWNED, "结款驳回": _OWNED}),
        Row(
            "metrics",
            "action",
            {
                "待财务付款": Cell(
                    grey=(R,), hint=lambda d: f"结款单{d.settlement_status}，已结款后可录"
                ),
                "已发布": replace(
                    _OWNED,
                    gates=(
                        Gate("receiver_phone", "收件电话", in_dialog=True),
                        Gate("brand_comment", "品牌词评论截图"),
                    ),
                ),
            },
        ),
        Row(
            "freight_submit",
            "action",
            {"已发布": Cell(edit=(W, PAY), read=(R,), reason="需同时持推广写与付款权限")},
        ),
        Row("comment", "action", {"待推送": Cell(edit=(W,), grey=(R,), hint="只读用户不能留言")}),
        Row(
            "quote_amount",
            "field",
            {"待推送": Cell(edit=(FieldWrite("promotion", "quote_amount"), W), read=(R,))},
        ),
        Row(
            "payment_qr",
            "field",
            {
                "待财务付款": (
                    Cell(when=(SettlementIn("待付款"),), edit=(W, Owner()), read=(R,)),
                    Cell(when=(SettlementIn("待财务付款"),), read=(R,)),
                )
            },
        ),
        Row(
            "shipping",
            "field",
            {
                "待推送": Cell(grey=(R,), hint="推送后由仓库回填"),
                "已发布": Cell(read=(R,)),
            },
        ),
        Row("settlement", "field", {"待财务付款": Cell(edit=(PAY,), read=(R,))}),
    ),
    patch_groups={"quote_amount": "quote_amount", "payment_qr_attachment_id": "payment_qr"},
)

TOY_DOCS: dict[str, FlowDocBase] = {
    "待推送": toy_doc("待推送"),
    "待财务付款·待付款": toy_doc("待财务付款", settlement_status="待付款"),
    "待财务付款·待财务付款": toy_doc("待财务付款", settlement_status="待财务付款"),
    "结款驳回": toy_doc("结款驳回", settlement_status="已驳回"),
    "已发布": toy_doc("已发布"),
}

# 紧凑写法：值是 7 个字，顺序 = PERSONA_ORDER（pr pr2 pr_manager admin finance operations warehouse）；
# 没列出的 (阶段标签, 行) 全是「隐」。
# 玩具矩阵的「读」只认 promotion:read 通配；仓库 060 起收回了 promotion:read，所以仓库列全是「隐」
_ALL_OWNED = "改读读读隐读隐"
TOY_EXPECTED_COMPACT: dict[tuple[str, str, str], str] = {
    ("待推送", "action", "ship_push"): _ALL_OWNED,
    ("待推送", "action", "cancel"): _ALL_OWNED,
    ("已发布", "action", "cancel"): _ALL_OWNED,
    ("待推送", "action", "recall_start"): "灰灰灰灰隐灰隐",
    ("已发布", "action", "recall_start"): _ALL_OWNED,
    ("已发布", "action", "urge"): "改读改改隐读隐",
    ("已发布", "action", "review"): "读改改读隐读隐",
    ("待财务付款·待付款", "action", "settlement_resubmit"): _ALL_OWNED,
    ("待财务付款·待财务付款", "action", "settlement_resubmit"): _ALL_OWNED,
    ("结款驳回", "action", "settlement_resubmit"): _ALL_OWNED,
    ("待财务付款·待付款", "action", "metrics"): "灰灰灰灰隐灰隐",
    ("待财务付款·待财务付款", "action", "metrics"): "灰灰灰灰隐灰隐",
    ("已发布", "action", "metrics"): _ALL_OWNED,
    ("已发布", "action", "freight_submit"): "读读读改隐读隐",
    ("待推送", "action", "comment"): "改改改改隐灰隐",
    ("待推送", "field", "quote_amount"): "改改改改隐读隐",
    ("待财务付款·待付款", "field", "payment_qr"): _ALL_OWNED,
    ("待财务付款·待财务付款", "field", "payment_qr"): "读读读读隐读隐",
    ("待推送", "field", "shipping"): "灰灰灰灰隐灰隐",
    ("已发布", "field", "shipping"): "读读读读隐读隐",
    ("待财务付款·待付款", "field", "settlement"): "读读读改改读隐",
    ("待财务付款·待财务付款", "field", "settlement"): "读读读改改读隐",
}


def _expand(compact: Mapping[tuple[str, str, str], str]) -> dict[GridKey, str]:
    out: dict[GridKey, str] = {}
    for label in TOY_DOCS:
        for row in TOY_MATRIX.rows:
            levels = compact.get((label, row.kind, row.key), "隐" * len(PERSONA_ORDER))
            assert len(levels) == len(PERSONA_ORDER)
            for name, level in zip(PERSONA_ORDER, levels, strict=True):
                out[(label, name, row.kind, row.key)] = level
    return out


@pytest.fixture
def toy(monkeypatch: pytest.MonkeyPatch) -> Iterator[Matrix]:
    monkeypatch.setitem(MATRICES, "promotion", TOY_MATRIX)
    yield TOY_MATRIX


def _ui(who: str | FlowActor, label: str, **override: Any) -> mx.UiState:
    doc = TOY_DOCS[label]
    if override:
        doc = replace(doc, **override)
    return ui_for(PERSONAS[who] if isinstance(who, str) else who, doc)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------


def test_key_constants_counts_and_unique() -> None:
    counts = {k: (len(ACTION_KEYS[k]), len(PAGE_ACTION_KEYS[k]), len(FIELD_KEYS[k])) for k in KINDS}
    assert counts == {
        "negotiation": (8, 1, 8),
        "promotion": (20, 2, 20),
        "warehouse": (1, 1, 0),
        "settlement": (5, 1, 0),
        "freight": (4, 0, 0),
    }
    for table in (ACTION_KEYS, PAGE_ACTION_KEYS, FIELD_KEYS):
        assert set(table) == set(KINDS)
        for keys in table.values():
            assert len(set(keys)) == len(keys)
            assert all(k.isidentifier() for k in keys)


def test_rule_items_use_registered_rules() -> None:
    assert {Owner.rule, NotPrOwner.rule, NoStar.rule} <= set(FLOW_RULES)


# ---------------------------------------------------------------------------
# 逐格比对
# ---------------------------------------------------------------------------


def test_toy_grid_matches_expected(toy: Matrix) -> None:
    grid = render_grid(toy, TOY_DOCS, PERSONAS)
    expected = _expand(TOY_EXPECTED_COMPACT)
    assert grid.keys() == expected.keys()
    diff = {k: (grid[k], expected[k]) for k in grid if grid[k] != expected[k]}
    assert diff == {}
    # 场景有效性：四种取值都出现过
    assert set(grid.values()) == {"改", "读", "灰", "隐"}


def test_expected_table_covers_registered_matrices() -> None:
    assert set(EXPECTED) == set(MATRICES)
    assert set(DOCS) == set(MATRICES)


def test_real_matrices_match_expected() -> None:
    if not MATRICES:
        pytest.skip(
            "PR-1 真实矩阵为空（设计 10.1：PR-2 起每个 PR 把碰到的行加进 MATRICES 与期望表）"
        )
    for kind, matrix in MATRICES.items():
        grid = render_grid(matrix, DOCS[kind], PERSONAS)
        assert grid == EXPECTED[kind], kind


def test_real_matrices_have_no_dead_rows() -> None:
    if not MATRICES:
        pytest.skip("PR-1 真实矩阵为空，没有行可查死键")
    for kind, matrix in MATRICES.items():
        assert dead_rows(render_grid(matrix, DOCS[kind], PERSONAS)) == [], kind


def test_dead_row_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    dead = Row("urge_complete", "action", {"已发布": Cell(edit=None, read=None)})
    m = replace(TOY_MATRIX, rows=(*TOY_MATRIX.rows, dead))
    monkeypatch.setitem(MATRICES, "promotion", m)
    assert dead_rows(render_grid(m, TOY_DOCS, PERSONAS)) == ["action:urge_complete"]
    monkeypatch.setitem(MATRICES, "promotion", TOY_MATRIX)
    assert dead_rows(render_grid(TOY_MATRIX, TOY_DOCS, PERSONAS)) == []


# ---------------------------------------------------------------------------
# 出口 1：ui_for
# ---------------------------------------------------------------------------


def test_precise_state_first(toy: Matrix) -> None:
    """结款重提只在结款驳回出现；待财务付款时格是「改」也不出现这个键（G7）。"""
    for label in ("待财务付款·待付款", "待财务付款·待财务付款"):
        assert "settlement_resubmit" not in _ui("pr", label).actions
    assert _ui("pr", "结款驳回").actions["settlement_resubmit"] == mx.UiAction("enabled")


def test_grey_when_state_disallows(toy: Matrix) -> None:
    """状态机不允许 + 矩阵标灰 → grey + hint（G9）。"""
    assert _ui("pr", "待推送").actions["recall_start"] == mx.UiAction(
        "grey", hint="还没发货，直接取消即可"
    )
    assert _ui("operations", "待推送").actions["recall_start"].state == "grey"
    assert "recall_start" not in _ui("finance", "待推送").actions
    # 提示可以按快照算
    assert _ui("pr", "待财务付款·待财务付款").actions["metrics"] == mx.UiAction(
        "grey", hint="结款单待财务付款，已结款后可录"
    )


def test_disallowed_without_grey_is_hidden(toy: Matrix) -> None:
    """已发布时 cancel 的格是「改」，但状态机不允许、又没标灰 → 不出现键。"""
    assert cell_level(PERSONAS["pr"], TOY_DOCS["已发布"], "action", "cancel") is Level.EDIT
    assert "cancel" not in _ui("pr", "已发布").actions


def test_grey_level_when_allowed(toy: Matrix) -> None:
    assert _ui("operations", "待推送").actions["comment"] == mx.UiAction(
        "grey", hint="只读用户不能留言"
    )
    assert _ui("pr", "待推送").actions["comment"] == mx.UiAction("enabled")


def test_gates_in_dialog(toy: Matrix) -> None:
    # 全齐 → enabled，无 missing
    assert _ui("pr", "待推送").actions["ship_push"] == mx.UiAction("enabled")
    # 只缺弹窗里能补的 → 仍 enabled，missing 照给
    assert _ui("pr", "待推送", color_size=None).actions["ship_push"] == mx.UiAction(
        "enabled", missing=(("color_size", "颜色尺码"),)
    )
    # 缺在别处 → disabled + 「缺：…」+ missing
    assert _ui("pr2", "已发布", brand_comment=None).actions["review"] == mx.UiAction(
        "disabled", reason="缺：品牌词评论截图", missing=(("brand_comment", "品牌词评论截图"),)
    )
    # 混合：只缺弹窗项 → enabled；两种都缺 → disabled，missing 两条都在
    assert _ui("pr", "已发布", receiver_phone="").actions["metrics"] == mx.UiAction(
        "enabled", missing=(("receiver_phone", "收件电话"),)
    )
    both = _ui("pr", "已发布", receiver_phone="", brand_comment=None).actions["metrics"]
    assert both.state == "disabled"
    assert both.missing == (("receiver_phone", "收件电话"), ("brand_comment", "品牌词评论截图"))
    assert both.reason == "缺：收件电话、品牌词评论截图"


def test_read_reason(toy: Matrix) -> None:
    # edit 里第一个不满足的是规则项 → 用规则项的 reason
    assert _ui("pr2", "待推送").actions["ship_push"] == mx.UiAction(
        "disabled", reason=Owner().reason
    )
    assert _ui("pr", "已发布").actions["review"] == mx.UiAction(
        "disabled", reason=NotPrOwner().reason
    )
    # 第一个不满足的是能力项 → 用格的 reason
    assert _ui("operations", "已发布").actions["review"] == mx.UiAction(
        "disabled", reason="需 PR 主管审核"
    )


def test_combined_roles_take_widest(toy: Matrix) -> None:
    """兼任 PR + 财务：判断对权限并集做，两边单独都到不了的「改」合起来能到。"""
    both = actor("pr", "finance", user_id=PR_ID)
    assert _ui("pr", "已发布").actions["freight_submit"].state == "disabled"
    assert "freight_submit" not in _ui("finance", "已发布").actions
    assert _ui(both, "已发布").actions["freight_submit"] == mx.UiAction("enabled")
    # 字段：财务单独 = 改、PR 单独 = 读，合起来取改
    assert _ui(both, "待财务付款·待付款").fields["settlement"] == mx.UiField("edit")


def test_star_and_nostar(toy: Matrix) -> None:
    admin = _ui("admin", "已发布")
    assert admin.actions["review"] == mx.UiAction("disabled", reason=NoStar().reason)
    assert admin.actions["freight_submit"] == mx.UiAction("enabled")
    assert _ui("admin", "待推送").fields["quote_amount"] == mx.UiField("edit")
    # 管理员不是本人：Owner 照样挡
    assert _ui("admin", "待推送").actions["ship_push"].state == "disabled"


def test_owner_or_reviewer(toy: Matrix) -> None:
    """「本人 或 审核权」：本人、主管、管理员（靠 *）改；别的 PR 读、原因是规则项的。"""
    assert _ui("pr", "已发布").actions["urge"] == mx.UiAction("enabled")
    assert _ui("pr_manager", "已发布").actions["urge"] == mx.UiAction("enabled")
    assert _ui("admin", "已发布").actions["urge"] == mx.UiAction("enabled")
    assert _ui("pr2", "已发布").actions["urge"] == mx.UiAction("disabled", reason=Owner().reason)
    # 先挂的是能力项（缺推广写）→ 格的 reason
    assert _ui("operations", "已发布").actions["urge"] == mx.UiAction(
        "disabled", reason="由负责 PR 或主管操作"
    )


def test_temporary_delegate_gets_edit(toy: Matrix) -> None:
    """临时授权的代理人：运营 + 个人授权 promotion.review:approve → 审核「改」。"""
    ops = PERSONAS["operations"]
    delegate = replace(
        ops,
        perms=EffectivePermissions(
            user_id=ops.perms.user_id, scopes=ops.perms.scopes | {"promotion.review:approve"}
        ),
    )
    assert _ui(delegate, "已发布").actions["review"] == mx.UiAction("enabled")


def test_settlement_in_predicate(toy: Matrix) -> None:
    assert _ui("pr", "待财务付款·待付款").fields["payment_qr"] == mx.UiField("edit")
    assert _ui("pr", "待财务付款·待财务付款").fields["payment_qr"] == mx.UiField("read")
    # 两个谓词都不命中 → 隐
    assert "payment_qr" not in _ui("pr", "待财务付款·待付款", settlement_status="已付款").fields


def test_field_grey_and_hidden(toy: Matrix) -> None:
    assert _ui("pr", "待推送").fields["shipping"] == mx.UiField("grey", hint="推送后由仓库回填")
    assert "shipping" not in _ui("finance", "待推送").fields
    assert _ui("operations", "待推送").fields["quote_amount"] == mx.UiField("read")


def test_predicate_on_wrong_doc_raises(toy: Matrix) -> None:
    doc = BareDoc(stage="待财务付款", state="待财务付款", owner_id=PR_ID)
    with pytest.raises(AttributeError):
        ui_for(PERSONAS["pr"], doc)


def test_gate_on_wrong_doc_raises(toy: Matrix) -> None:
    doc = BareDoc(stage="待推送", state="待推送", owner_id=PR_ID)
    with pytest.raises(AttributeError):
        ui_for(PERSONAS["pr"], doc)


def test_to_dict_views(toy: Matrix) -> None:
    ui = _ui("pr", "待推送", color_size=None, column="A")
    listed = ui.to_dict(view="list")
    assert listed == {
        "column": "A",
        "actions": {
            "ship_push": {
                "state": "enabled",
                "missing": [{"key": "color_size", "label": "颜色尺码"}],
            },
            "cancel": {"state": "enabled"},
            "recall_start": {"state": "grey", "hint": "还没发货，直接取消即可"},
            "comment": {"state": "enabled"},
        },
        "edits": ["quote_amount"],
    }
    detail = ui.to_dict(view="detail")
    assert "edits" not in detail
    assert detail["fields"] == {
        "quote_amount": {"state": "edit"},
        "shipping": {"state": "grey", "hint": "推送后由仓库回填"},
    }
    # 没有 column 就不出这个键；读的动作带 reason
    ops = _ui("operations", "待推送").to_dict(view="list")
    assert "column" not in ops
    assert ops["actions"]["ship_push"] == {"state": "disabled", "reason": "由负责 PR 操作"}
    with pytest.raises(ValueError):
        ui.to_dict(view="drawer")  # type: ignore[arg-type]


def test_actions_follow_constant_order(toy: Matrix) -> None:
    keys = list(_ui("pr", "已发布").actions)
    order = ACTION_KEYS["promotion"]
    assert keys == sorted(keys, key=order.index)
    assert len(keys) >= 3


def test_unregistered_kind_raises() -> None:
    @dataclass(frozen=True, kw_only=True)
    class NegDoc(FlowDocBase):
        kind: ClassVar[str] = "negotiation"

        def allowed_actions(self) -> frozenset[str]:
            return frozenset()

    doc = NegDoc(stage="草稿", state="草稿")
    for call in (
        lambda: ui_for(PERSONAS["pr"], doc),
        lambda: require(PERSONAS["pr"], doc, "edit"),
        lambda: writable_fields(PERSONAS["pr"], doc),
    ):
        with pytest.raises(KeyError):
            call()


def test_unknown_stage_or_action_is_programming_error(toy: Matrix) -> None:
    with pytest.raises(ValueError):
        ui_for(PERSONAS["pr"], toy_doc("已完结"))
    with pytest.raises(ValueError):
        require(PERSONAS["pr"], TOY_DOCS["待推送"], "ship_pushh")


def test_base_doc_must_implement_allowed_actions() -> None:
    @dataclass(frozen=True, kw_only=True)
    class Lazy(FlowDocBase):
        kind: ClassVar[str] = "promotion"

    with pytest.raises(NotImplementedError):
        Lazy(stage="待推送", state="待推送").allowed_actions()


# ---------------------------------------------------------------------------
# Matrix 构造校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kw", "needle"),
    [
        ({"kind": "order"}, "未知单据类型"),
        ({"rows": (Row("ship_pushh", "action", {}),)}, "ship_pushh"),
        ({"rows": (Row("receiver", "action", {}),)}, "receiver"),  # 字段键当动作键用
        ({"rows": (Row("cancel", "action", {"已完结": Cell()}),)}, "已完结"),
        ({"rows": (Row("cancel", "action", {}), Row("cancel", "action", {}))}, "重复"),
        ({"rows": (Row("cancel", "action", {"待推送": Cell(grey=(R,))}),)}, "提示"),
        (
            {"rows": (Row("shipping", "field", {"待推送": Cell(gates=(Gate("x", "x"),))}),)},
            "卡点",
        ),
        ({"patch_groups": {"note_title": "other_info"}}, "other_info"),
        ({"stages": ("待推送", "待推送")}, "重复"),
    ],
)
def test_matrix_validation(kw: dict[str, Any], needle: str) -> None:
    base: dict[str, Any] = {"kind": "promotion", "stages": ("待推送",), "rows": ()}
    base.update(kw)
    with pytest.raises(ValueError, match=needle):
        Matrix(**base)


def test_any_of() -> None:
    with pytest.raises(TypeError):
        AnyOf(Scope("promotion", "write"), "promotion:write")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        AnyOf()
    who = PERSONAS["finance"]
    assert AnyOf(W, PAY).ok(who, TOY_DOCS["已发布"])
    assert not AnyOf(W, R).ok(who, TOY_DOCS["已发布"])
    # 规则项也能组合：任一满足即过
    assert AnyOf(Owner(), NEG_REVIEW).ok(PERSONAS["pr"], TOY_DOCS["已发布"])
    assert AnyOf(Owner(), NEG_REVIEW).ok(PERSONAS["pr_manager"], TOY_DOCS["已发布"])
    assert not AnyOf(Owner(), NEG_REVIEW).ok(PERSONAS["pr2"], TOY_DOCS["已发布"])
    # 归类：第一个规则项（嵌套也算）；一个规则项都没有 = 自己（缺权限）
    either = AnyOf(NEG_REVIEW, Owner())
    assert either.blame == Owner()
    nested = AnyOf(NEG_REVIEW, AnyOf(R, NoStar()), Owner())
    assert nested.blame == NoStar()
    caps = AnyOf(W, R)
    assert caps.blame is caps
    outer = AnyOf(NEG_REVIEW, caps)
    assert outer.blame is outer


def test_stage_in_predicate() -> None:
    assert StageIn("待推送", "已发布").matches(TOY_DOCS["已发布"])
    assert not StageIn("待推送").matches(TOY_DOCS["已发布"])
    with pytest.raises(ValueError):
        StageIn()


# ---------------------------------------------------------------------------
# 出口 2：require
# ---------------------------------------------------------------------------


def _code(exc: pytest.ExceptionInfo[AppException]) -> str:
    return exc.value.code


def test_require_state_machine_first(toy: Matrix) -> None:
    """状态机不允许优先：哪怕规则也不满足、卡点也缺。"""
    doc = replace(TOY_DOCS["待财务付款·待付款"], owner_id=uuid4())
    with pytest.raises(AppException) as exc:
        require(PERSONAS["pr"], doc, "settlement_resubmit")
    assert _code(exc) == "ILLEGAL_STATE_TRANSITION"
    assert exc.value.status_code == 422
    assert exc.value.details == {"from_state": "待财务付款", "action": "settlement_resubmit"}


def test_require_rule_before_gate(toy: Matrix) -> None:
    """规则不满足 + 卡点也缺 → 先报规则（F4-a），reason 与按钮悬停文案同一份。"""
    doc = replace(TOY_DOCS["已发布"], brand_comment=None)
    with pytest.raises(AppException) as exc:
        require(PERSONAS["pr"], doc, "review")
    assert _code(exc) == "FLOW_ACTION_FORBIDDEN"
    assert exc.value.status_code == 403
    assert exc.value.details == {"rule": "self_review", "reason": NotPrOwner().reason}
    assert ui_for(PERSONAS["pr"], doc).actions["review"].reason == exc.value.details["reason"]

    with pytest.raises(AppException) as exc:
        require(PERSONAS["admin"], doc, "review")
    assert _code(exc) == "FLOW_ACTION_FORBIDDEN"
    assert exc.value.details["rule"] == "star_first_level"


def test_require_owner_or_reviewer(toy: Matrix) -> None:
    """「或」整体失败按第一个规则项报（7.2：其他人 → 403 not_owner），reason 与悬停文案同一份。"""
    doc = TOY_DOCS["已发布"]
    for who in ("pr", "pr_manager", "admin"):
        require(PERSONAS[who], doc, "urge")
    with pytest.raises(AppException) as exc:
        require(PERSONAS["pr2"], doc, "urge")
    assert _code(exc) == "FLOW_ACTION_FORBIDDEN"
    assert exc.value.details == {"rule": "not_owner", "reason": Owner().reason}
    assert ui_for(PERSONAS["pr2"], doc).actions["urge"].reason == exc.value.details["reason"]


def test_require_any_of_capabilities_is_permission_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「或」里全是能力项、全不满足 → 缺权限，不是规则 403。"""
    row = Row("urge", "action", {"已发布": Cell(edit=(AnyOf(PAY, NEG_REVIEW),), read=(R,))})
    m = replace(TOY_MATRIX, rows=(*(r for r in TOY_MATRIX.rows if r.key != "urge"), row))
    monkeypatch.setitem(MATRICES, "promotion", m)
    with pytest.raises(AppException) as exc:
        require(PERSONAS["pr"], TOY_DOCS["已发布"], "urge")
    assert _code(exc) == "PERMISSION_DENIED"
    require(PERSONAS["finance"], TOY_DOCS["已发布"], "urge")


def test_require_capability_failure_is_permission_denied(toy: Matrix) -> None:
    with pytest.raises(AppException) as exc:
        require(PERSONAS["operations"], TOY_DOCS["已发布"], "review")
    assert _code(exc) == "PERMISSION_DENIED"
    # 界面上是「隐」的人直接调接口，同样是缺权限
    assert "ship_push" not in _ui("finance", "待推送").actions
    with pytest.raises(AppException) as exc:
        require(PERSONAS["finance"], TOY_DOCS["待推送"], "ship_push")
    assert _code(exc) == "PERMISSION_DENIED"


def test_require_gate_missing_matches_ui(toy: Matrix) -> None:
    doc = replace(TOY_DOCS["已发布"], receiver_phone="", brand_comment=None)
    with pytest.raises(AppException) as exc:
        require(PERSONAS["pr"], doc, "metrics")
    assert _code(exc) == "FLOW_GATE_MISSING"
    assert exc.value.status_code == 422
    ui_missing = ui_for(PERSONAS["pr"], doc).actions["metrics"].missing
    assert exc.value.details["missing"] == [{"key": k, "label": lb} for k, lb in ui_missing]
    # in_dialog 的缺项按钮可点，但 require 照样拦（弹窗提交时 service 应已补进快照）
    with pytest.raises(AppException) as exc:
        require(PERSONAS["pr"], replace(TOY_DOCS["待推送"], color_size=None), "ship_push")
    assert _code(exc) == "FLOW_GATE_MISSING"
    assert exc.value.details["missing"] == [{"key": "color_size", "label": "颜色尺码"}]


def test_require_passes(toy: Matrix) -> None:
    require(PERSONAS["pr"], TOY_DOCS["待推送"], "ship_push")
    require(PERSONAS["pr2"], TOY_DOCS["已发布"], "review")
    require(actor("pr", "finance"), TOY_DOCS["已发布"], "freight_submit")


def test_require_no_cell_is_permission_denied(toy: Matrix) -> None:
    """状态允许、但这一阶段没有这一行的格（comment 只在待推送有格）。"""
    with pytest.raises(AppException) as exc:
        require(PERSONAS["pr"], TOY_DOCS["已发布"], "comment")
    assert _code(exc) == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# 出口 3：PATCH 字段
# ---------------------------------------------------------------------------


def test_writable_fields(toy: Matrix) -> None:
    wf = writable_fields(PERSONAS["pr"], TOY_DOCS["待推送"])
    assert wf.managed == {"quote_amount", "payment_qr_attachment_id"}
    assert wf.allowed == {"quote_amount"}
    assert writable_fields(PERSONAS["pr"], TOY_DOCS["待财务付款·待付款"]).allowed == {
        "payment_qr_attachment_id"
    }
    assert writable_fields(PERSONAS["operations"], TOY_DOCS["待推送"]).allowed == frozenset()


def test_ensure_patch_allowed(toy: Matrix) -> None:
    # 没入矩阵的字段放行（过渡规则）
    ensure_patch_allowed(PERSONAS["pr"], TOY_DOCS["待推送"], {"quote_amount", "note_title"})
    ensure_patch_allowed(PERSONAS["operations"], TOY_DOCS["待推送"], {"note_title"})
    # 越权的一次全列出来（G2）
    with pytest.raises(FieldPermissionDenied) as exc:
        ensure_patch_allowed(
            PERSONAS["operations"],
            TOY_DOCS["待推送"],
            {"quote_amount", "payment_qr_attachment_id", "note_title"},
        )
    assert exc.value.code == "FIELD_PERMISSION_DENIED"
    # 顺序按 patch_groups 的登记顺序（不是字母序），field = 第一个
    assert exc.value.details["fields"] == ["quote_amount", "payment_qr_attachment_id"]
    assert exc.value.details["field"] == "quote_amount"
    assert exc.value.details["entity"] == "promotion"
    with pytest.raises(FieldPermissionDenied) as exc:
        ensure_patch_allowed(PERSONAS["pr"], TOY_DOCS["待推送"], ["payment_qr_attachment_id"])
    assert exc.value.details["fields"] == ["payment_qr_attachment_id"]


# ---------------------------------------------------------------------------
# 错误码
# ---------------------------------------------------------------------------


def test_flow_error_shapes() -> None:
    e1 = FlowActionForbiddenError(rule="not_owner", reason="只有负责人本人可以操作")
    assert (e1.code, e1.status_code) == ("FLOW_ACTION_FORBIDDEN", 403)
    assert e1.details == {"rule": "not_owner", "reason": "只有负责人本人可以操作"}
    assert e1.message == "只有负责人本人可以操作"
    with pytest.raises(ValueError):
        FlowActionForbiddenError(rule="nobody", reason="x")

    e2 = FlowGateMissingError([("receiver_phone", "收件电话"), ("brand_comment", "品牌词评论截图")])
    assert (e2.code, e2.status_code) == ("FLOW_GATE_MISSING", 422)
    assert e2.details == {
        "missing": [
            {"key": "receiver_phone", "label": "收件电话"},
            {"key": "brand_comment", "label": "品牌词评论截图"},
        ]
    }
    assert e2.message == "缺：收件电话、品牌词评论截图"
    with pytest.raises(ValueError):
        FlowGateMissingError([])
