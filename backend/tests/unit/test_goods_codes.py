"""补充 3：新建商品的编码生成规则（设计 §11.2，FR-9，AC 64、65）。

用假仓储（只有 ``code_exists`` / ``get_by_code``）直接测 ``generate_goods_code``，不连库。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from app.modules.product import goods_codes
from app.modules.product.goods_codes import (
    GOODS_CODE_MAX_LEN,
    NOTICE_OCCUPIED_BY_DELETED,
    GoodsCodeExhaustedError,
    generate_goods_code,
)

pytestmark = pytest.mark.unit


@dataclass
class _Holder:
    goods_title: str
    short_name: str | None
    is_deleted: bool = False


class _FakeRepo:
    def __init__(self, taken: dict[str, _Holder] | None = None, *, all_taken: bool = False):
        self.taken = dict(taken or {})
        self.all_taken = all_taken
        self.checked: list[str] = []

    async def code_exists(self, goods_code: str) -> bool:
        self.checked.append(goods_code)
        return self.all_taken or goods_code in self.taken

    async def get_by_code(
        self, goods_code: str, *, include_deleted: bool = False
    ) -> _Holder | None:
        h = self.taken.get(goods_code)
        if h is None or (h.is_deleted and not include_deleted):
            return None
        return h


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(goods_codes, "_today", lambda: "20261007")
    monkeypatch.setattr(goods_codes, "_random_hex", lambda n: "ab" * n)


class TestSingle:
    async def test_code_is_style_code(self) -> None:
        assert await generate_goods_code(_FakeRepo(), ["260415"]) == ("260415", None)

    async def test_occupied_appends_suffix_with_notice(self) -> None:
        repo = _FakeRepo(
            {
                "260415": _Holder("冰雪飞狐皮草外套长款", "冰雪飞狐"),
                "260415-2": _Holder("另一个", None),
            }
        )
        code, notice = await generate_goods_code(repo, ["260415"])
        assert code == "260415-3"
        assert notice == "该款已有商品「冰雪飞狐」，已为新商品另行生成内部编码"
        assert "260415" not in notice

    async def test_occupied_by_deleted_only(self) -> None:
        repo = _FakeRepo({"260415": _Holder("已删商品", None, is_deleted=True)})
        code, notice = await generate_goods_code(repo, ["260415"])
        assert code == "260415-2"
        assert notice == NOTICE_OCCUPIED_BY_DELETED

    async def test_display_name_falls_back_to_title(self) -> None:
        repo = _FakeRepo({"A1": _Holder("没填简称的全称", None)})
        _, notice = await generate_goods_code(repo, ["A1"])
        assert notice is not None and "「没填简称的全称」" in notice

    async def test_long_style_code_skips_overlong_suffix(self) -> None:
        code64 = "A" * 64
        repo = _FakeRepo({code64: _Holder("长", None)})
        code, _ = await generate_goods_code(repo, [code64])
        # 款号-2 超过 64 字，不试；直接走兜底
        assert code == "G-20261007-abababab"
        assert all(len(c) <= GOODS_CODE_MAX_LEN for c in repo.checked)


class TestSuit:
    async def test_combined_sorted(self) -> None:
        code, notice = await generate_goods_code(_FakeRepo(), ["260419", "260415"])
        assert code == "SUIT-260415_260419"
        assert notice is None

    async def test_same_members_again_gets_suffix(self) -> None:
        repo = _FakeRepo({"SUIT-A_B": _Holder("套装", None, is_deleted=True)})
        code, notice = await generate_goods_code(repo, ["B", "A"])
        assert code == "SUIT-A_B-2"
        assert notice is None

    async def test_too_long_uses_serial(self) -> None:
        members = [f"STYLE{i:02d}LONGCODE" for i in range(5)]
        code, _ = await generate_goods_code(_FakeRepo(), members)
        assert code == "SUIT-20261007-ababab"

    async def test_platform_id_like_uses_serial(self) -> None:
        code, _ = await generate_goods_code(_FakeRepo(), ["1074568657697", "260415"])
        assert code == "SUIT-20261007-ababab"
        assert not re.search(r"\d{9,}", code.replace("20261007", ""))

    async def test_serial_occupied_appends_suffix(self) -> None:
        repo = _FakeRepo({"SUIT-20261007-ababab": _Holder("x", None)})
        code, _ = await generate_goods_code(repo, ["123456789", "X"])
        assert code == "SUIT-20261007-ababab-2"


class TestFallback:
    async def test_all_suffixes_taken_falls_back(self) -> None:
        taken = {"SUIT-A_B": _Holder("x", None)}
        taken.update({f"SUIT-A_B-{n}": _Holder("x", None) for n in range(2, 100)})
        code, _ = await generate_goods_code(_FakeRepo(taken), ["A", "B"])
        assert code == "G-20261007-abababab"

    async def test_everything_taken_raises(self) -> None:
        repo = _FakeRepo(all_taken=True)
        with pytest.raises(GoodsCodeExhaustedError):
            await generate_goods_code(repo, ["A", "B"])
        # 99 个组合候选 + 5 次兜底
        assert len(repo.checked) == 99 + 5

    async def test_empty_members_rejected(self) -> None:
        with pytest.raises(ValueError):
            await generate_goods_code(_FakeRepo(), [])


async def test_inputs_are_only_style_codes() -> None:
    """签名只收成员款号：编码由款号推出，不可能带上平台 ID。"""
    code, _ = await generate_goods_code(_FakeRepo(), ["260415", "260419"])
    assert code.startswith("SUIT-")
    assert set(code.removeprefix("SUIT-").split("_")) == {"260415", "260419"}
    assert len(code) <= GOODS_CODE_MAX_LEN
