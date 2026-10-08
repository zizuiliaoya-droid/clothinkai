"""8a-4 ``decide_goods_target``：§5.3 六行，按顺序判断、命中即停（先找可写目标、最后才看编码占用）。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.importer.adapters.style_sku_goods import (
    GoodsAction,
    GoodsCandidate,
    GoodsDecision,
    decide_goods_target,
)

STYLE_CODE = "260419"


def _g(
    code: str,
    name: str,
    *,
    suit: bool = False,
    deleted: bool = False,
    contains: bool = True,
    members: int = 1,
) -> GoodsCandidate:
    return GoodsCandidate(
        id=uuid4(),
        goods_code=code,
        display_name=name,
        is_suit=suit,
        is_deleted=deleted,
        contains_style=contains,
        member_count=members,
    )


def _no_codes(decision: GoodsDecision, *goods: GoodsCandidate) -> None:
    """提示与冲突文案里不出现任何商品编码（FR-8）。"""
    texts = [*decision.warnings, decision.conflict_message or ""]
    for g in goods:
        for t in texts:
            assert g.goods_code not in t


def _code_goods(kind: str) -> GoodsCandidate:
    """编码 = 款号、但不可写的三种占用。"""
    if kind == "deleted":
        return _g(STYLE_CODE, "已删商品", deleted=True, contains=False, members=0)
    if kind == "suit":
        return _g(STYLE_CODE, "某套装", suit=True, contains=False, members=0)
    return _g(STYLE_CODE, "别款单品", contains=False, members=0)


_OCCUPIED_TEXT = {"deleted": "已删除的商品", "suit": "套装", "members": "成员不同的商品"}


class TestSixRows:
    def test_row1_code_goods_only_this_style(self) -> None:
        code_goods = _g(STYLE_CODE, "主商品")
        other = _g(f"{STYLE_CODE}-1074568657697", "千牛单品")
        d = decide_goods_target(code_goods, (code_goods, other))
        assert (d.action, d.rule, d.target) == (GoodsAction.WRITE, 1, code_goods)
        assert d.warnings == ("另有单品商品 千牛单品 未写入（只写编码与款号相同的那个）",)
        _no_codes(d, code_goods, other)

    def test_row1_without_other_singles_no_warning(self) -> None:
        code_goods = _g(STYLE_CODE, "主商品")
        suit = _g("SUIT-1", "套装", suit=True, members=2)
        d = decide_goods_target(code_goods, (code_goods, suit))
        assert (d.action, d.rule, d.warnings) == (GoodsAction.WRITE, 1, ())

    def test_row2_exactly_one_single(self) -> None:
        single = _g(f"{STYLE_CODE}-777", "千牛单品")
        d = decide_goods_target(None, (single,))
        assert (d.action, d.rule, d.target, d.warnings) == (GoodsAction.WRITE, 2, single, ())

    def test_row3_two_singles(self) -> None:
        a = _g(f"{STYLE_CODE}-1", "甲")
        b = _g(f"{STYLE_CODE}-2", "乙")
        d = decide_goods_target(None, (a, b))
        assert (d.action, d.rule, d.target) == (GoodsAction.NONE, 3, None)
        assert d.warnings == ("该款属于多个单品商品（甲、乙），商品层未写入",)
        _no_codes(d, a, b)

    def test_row4_only_in_suit(self) -> None:
        suit = _g("SUIT-1074568657697", "套装", suit=True, members=2)
        d = decide_goods_target(None, (suit,))
        assert (d.action, d.rule) == (GoodsAction.NONE, 4)
        assert d.warnings == ("该款只在套装里，未建单品商品",)

    def test_row5_occupied_and_no_membership(self) -> None:
        code_goods = _code_goods("suit")
        d = decide_goods_target(code_goods, ())
        assert (d.action, d.rule, d.target) == (GoodsAction.KEY_CONFLICT, 5, code_goods)
        assert d.conflict_message == "款号对应的商品编码已被套装占用，未建单品商品"

    def test_row6_create(self) -> None:
        d = decide_goods_target(None, ())
        assert (d.action, d.rule, d.target, d.warnings) == (GoodsAction.CREATE, 6, None, ())

    def test_code_goods_two_members_containing_style_not_writable(self) -> None:
        """编码 = 款号的单品、成员不只本款 → 不是第 1 行；本款只有它一个单品 → 第 2 行写它。"""
        code_goods = _g(STYLE_CODE, "两款单品", members=2)
        d = decide_goods_target(code_goods, (code_goods,))
        assert (d.action, d.rule, d.target) == (GoodsAction.WRITE, 2, code_goods)


class TestOccupiedOrder:
    @pytest.mark.parametrize("kind", ["deleted", "suit", "members"])
    def test_occupied_with_one_single_goes_row2(self, kind: str) -> None:
        """占用 × 本款恰好 1 个单品 → 写那个单品、只提示、不记冲突（N12 / FI-30）。"""
        code_goods = _code_goods(kind)
        single = _g(f"{STYLE_CODE}-2", "在用单品")
        d = decide_goods_target(code_goods, (single,))
        assert (d.action, d.rule, d.target) == (GoodsAction.WRITE, 2, single)
        assert d.conflict_message is None
        assert d.warnings == (f"款号对应的编码被{_OCCUPIED_TEXT[kind]}占用，已写入「在用单品」",)
        _no_codes(d, code_goods, single)

    @pytest.mark.parametrize("kind", ["deleted", "suit", "members"])
    def test_occupied_without_membership_goes_row5(self, kind: str) -> None:
        code_goods = _code_goods(kind)
        d = decide_goods_target(code_goods, ())
        assert (d.action, d.rule) == (GoodsAction.KEY_CONFLICT, 5)
        assert d.conflict_message == (
            f"款号对应的商品编码已被{_OCCUPIED_TEXT[kind]}占用，未建单品商品"
        )
        _no_codes(d, code_goods)

    def test_one_single_plus_suits_goes_row2(self) -> None:
        """本款 1 个单品 + 若干套装 → 第 2 行（套装不算单品，FI-7）。"""
        single = _g(f"{STYLE_CODE}-9", "单品")
        suits = tuple(_g(f"SUIT-{i}", f"套装{i}", suit=True, members=2) for i in range(3))
        d = decide_goods_target(None, (single, *suits))
        assert (d.action, d.rule, d.target) == (GoodsAction.WRITE, 2, single)

    def test_suit_never_target(self) -> None:
        """只在套装里（套装编码 = 款号也一样）永远不写套装。"""
        suit = _g(STYLE_CODE, "套装", suit=True, members=2)
        d = decide_goods_target(suit, (suit,))
        assert d.action is GoodsAction.NONE
        assert d.target is None
