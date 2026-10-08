"""8a-6：同一批次里同一对象的同一字段以第一个已提交的给值行为准（设计 §4.2.1，N15f）。"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.modules.importer.compare import FieldSpec, ValueKind
from app.modules.importer.outcome import (
    BatchSeen,
    ImportRowContext,
    RowKind,
    merge_kinds,
    screen_batch_seen,
)

_OBJ = uuid4()
_SPECS = (
    FieldSpec("remark", "备注", ValueKind.TEXT),
    FieldSpec("quote", "报价", ValueKind.DECIMAL),
    FieldSpec("name", "商品名称", ValueKind.TEXT, create_only=True),
)


def _ctx(seen: BatchSeen, row: int) -> ImportRowContext:
    return ImportRowContext(
        tenant_id=uuid4(),
        source="manual_blogger",
        batch_id=uuid4(),
        row_number=row,
        actor_id=None,
        batch_seen=seen,
    )


def _key(field: str, obj: UUID = _OBJ) -> tuple[str, UUID, str]:
    return ("blogger", obj, field)


@pytest.mark.unit
class TestFirstDifferingRow:
    def test_unseen_stages_and_returns_none(self) -> None:
        seen = BatchSeen()
        assert seen.first_differing_row(_key("remark"), 1, "A") is None
        assert seen.staged == {_key("remark"): (1, "A")}
        assert seen.committed == {}

    def test_same_value_returns_none(self) -> None:
        seen = BatchSeen()
        seen.first_differing_row(_key("remark"), 1, "A")
        seen.commit_row()
        assert seen.first_differing_row(_key("remark"), 2, "A") is None

    def test_different_value_returns_first_row(self) -> None:
        seen = BatchSeen()
        seen.first_differing_row(_key("remark"), 3, "A")
        seen.commit_row()
        assert seen.first_differing_row(_key("remark"), 5, "B") == 3

    def test_commit_row_makes_it_effective(self) -> None:
        seen = BatchSeen()
        seen.first_differing_row(_key("remark"), 1, "A")
        assert seen.committed == {}
        seen.commit_row()
        assert seen.committed == {_key("remark"): (1, "A")}
        assert seen.staged == {}

    def test_discard_row_drops_staged(self) -> None:
        """登记之后这一行失败：它的值没进库，后面的行不会被「按第 M 行处理」挡掉（N15e）。"""
        seen = BatchSeen()
        seen.first_differing_row(_key("remark"), 1, "A")
        seen.discard_row()
        assert seen.staged == {}
        assert seen.first_differing_row(_key("remark"), 2, "B") is None

    def test_keys_are_per_object(self) -> None:
        seen = BatchSeen()
        seen.first_differing_row(_key("remark"), 1, "A")
        seen.commit_row()
        assert seen.first_differing_row(_key("remark", uuid4()), 2, "B") is None

    def test_fresh_batch_seen_starts_empty(self) -> None:
        """只重跑失败行时每次执行新建一个，从空开始（N15f）。"""
        first = BatchSeen()
        first.first_differing_row(_key("remark"), 1, "A")
        first.commit_row()
        again = BatchSeen()
        assert again.first_differing_row(_key("remark"), 2, "B") is None


@pytest.mark.unit
class TestFirstNotice:
    def test_once_per_batch_after_commit(self) -> None:
        seen = BatchSeen()
        assert seen.first_notice("image") is True
        assert seen.first_notice("image") is False  # 同一行再问一次
        seen.commit_row()
        assert seen.notified == {"image"}
        assert seen.staged_notices == set()
        assert seen.first_notice("image") is False
        assert seen.first_notice("other") is True

    def test_discarded_row_does_not_count(self) -> None:
        """登记提示的行失败了：后面第一个提交的行照样带上这条提示。"""
        seen = BatchSeen()
        assert seen.first_notice("image") is True
        seen.discard_row()
        assert seen.notified == set()
        assert seen.first_notice("image") is True


@pytest.mark.unit
class TestScreenBatchSeen:
    def test_inconsistent_field_removed_with_warning(self) -> None:
        seen = BatchSeen()
        screen_batch_seen(_ctx(seen, 1), "blogger", _OBJ, _SPECS, {"remark": "A"})
        seen.commit_row()
        kept, warnings = screen_batch_seen(
            _ctx(seen, 2), "blogger", _OBJ, _SPECS, {"remark": "B", "quote": "5"}
        )
        assert kept == {"quote": "5"}
        assert warnings == ["第 2 行的备注与第 1 行不一致，按第 1 行处理"]

    def test_missing_value_not_registered(self) -> None:
        seen = BatchSeen()
        kept, warnings = screen_batch_seen(
            _ctx(seen, 1), "blogger", _OBJ, _SPECS, {"remark": "  ", "quote": None}
        )
        assert kept == {"remark": "  ", "quote": None}
        assert warnings == []
        assert seen.staged == {}

    def test_create_only_not_registered(self) -> None:
        seen = BatchSeen()
        screen_batch_seen(_ctx(seen, 1), "blogger", _OBJ, _SPECS, {"name": "款名 A"})
        seen.commit_row()
        assert seen.committed == {}
        kept, warnings = screen_batch_seen(
            _ctx(seen, 2), "blogger", _OBJ, _SPECS, {"name": "款名 B"}
        )
        assert kept == {"name": "款名 B"}
        assert warnings == []

    def test_normalized_comparison(self) -> None:
        """60 与 60.00 归一后相同、文本去首尾空白：不算不一致。"""
        seen = BatchSeen()
        screen_batch_seen(_ctx(seen, 1), "blogger", _OBJ, _SPECS, {"quote": "60", "remark": "A"})
        seen.commit_row()
        kept, warnings = screen_batch_seen(
            _ctx(seen, 2), "blogger", _OBJ, _SPECS, {"quote": "60.00", "remark": " A "}
        )
        assert kept == {"quote": "60.00", "remark": " A "}
        assert warnings == []

    def test_discarded_registration_has_no_effect(self) -> None:
        seen = BatchSeen()
        screen_batch_seen(_ctx(seen, 1), "blogger", _OBJ, _SPECS, {"remark": "A"})
        seen.discard_row()
        kept, warnings = screen_batch_seen(_ctx(seen, 2), "blogger", _OBJ, _SPECS, {"remark": "B"})
        assert kept == {"remark": "B"}
        assert warnings == []


@pytest.mark.unit
class TestMergeKinds:
    @pytest.mark.parametrize(
        ("kinds", "expected"),
        [
            ([RowKind.SKIPPED, RowKind.CONFLICT, RowKind.INSERTED], RowKind.CONFLICT),
            ([RowKind.FILLED, RowKind.INSERTED], RowKind.INSERTED),
            ([RowKind.FILLED, RowKind.UPDATED], RowKind.UPDATED),
            ([RowKind.SKIPPED, RowKind.FILLED], RowKind.FILLED),
            ([RowKind.SKIPPED], RowKind.SKIPPED),
            ([], RowKind.SKIPPED),
        ],
    )
    def test_priority(self, kinds: list[RowKind], expected: RowKind) -> None:
        assert merge_kinds(kinds) is expected
