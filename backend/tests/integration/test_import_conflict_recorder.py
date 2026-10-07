"""8a-6：ConflictRecorder 的取代 / 合并规则（真库，设计 §4.4 两张规则表）。

用测试 session（外层事务 + savepoint，结束统一回滚），不经 runner。
「C 是本批次的」= ctx.batch_id 不为空且与 C.batch_id 相同；其余一律按「别的批次」。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.importer.compare import FieldDiff
from app.modules.importer.conflicts import ConflictRecorder, clip_label
from app.modules.importer.models import ImportConflict
from app.modules.importer.outcome import BatchSeen, ImportRowContext

SOURCE = "manual_blogger"


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


def _ctx(tenant: Any, batch_id: UUID | None, row: int, source: str = SOURCE) -> ImportRowContext:
    return ImportRowContext(
        tenant_id=tenant.id,
        source=source,
        batch_id=batch_id,
        row_number=row,
        actor_id=None,
        batch_seen=BatchSeen(),
    )


def _diff(field: str, system: JsonValue, file: JsonValue, label: str | None = None) -> FieldDiff:
    return FieldDiff(
        field=field,
        label=label or field,
        system=system,
        file=file,
        system_display=None if system is None else str(system),
        file_display=None if file is None else str(file),
    )


async def _all(session: AsyncSession, object_id: UUID) -> list[ImportConflict]:
    stmt = (
        select(ImportConflict)
        .where(ImportConflict.object_id == object_id)
        .order_by(ImportConflict.created_at, ImportConflict.status)
        .execution_options(populate_existing=True)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _record(
    session: AsyncSession,
    ctx: ImportRowContext,
    obj: UUID,
    diffs: list[FieldDiff],
    *,
    compared: set[str] | None = None,
    current: dict[str, JsonValue] | None = None,
    kind: str = "fields",
    message: str | None = None,
    label: str = "小美",
) -> list[str]:
    rec = ConflictRecorder(session, ctx)
    c = await rec.lock_pending("blogger", obj)
    return await rec.record(
        c,
        "blogger",
        obj,
        "xhs-key",
        label,
        kind=kind,
        diffs=diffs,
        compared=compared if compared is not None else {d.field for d in diffs},
        current=current or {},
        message=message,
    )


async def _touch(
    session: AsyncSession,
    ctx: ImportRowContext,
    obj: UUID,
    compared: set[str],
    incoming: dict[str, JsonValue] | None = None,
) -> list[str]:
    rec = ConflictRecorder(session, ctx)
    c = await rec.lock_pending("blogger", obj)
    return await rec.touch(c, "blogger", obj, compared=compared, incoming=incoming or {})


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("tenant_ctx")
class TestRecordNewAndSameBatch:
    async def test_new_pending(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        batch = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        warnings = await _record(
            session, _ctx(tenant_a, batch.id, 2), obj, [_diff("quote", "60.00", "65.00")]
        )
        assert warnings == []
        [c] = await _all(session, obj)
        assert (c.status, c.kind, c.batch_id, c.row_numbers) == ("pending", "fields", batch.id, [2])
        assert c.fields[0]["field"] == "quote"
        assert (c.fields[0]["system"], c.fields[0]["file"]) == ("60.00", "65.00")
        assert c.resolved_at is None
        assert c.object_label == "小美"

    async def test_same_batch_merges(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        """AC 43：同批多行合并成一条——追加行号、并入新字段；同字段文件值不同保留先到 + 提示。"""
        batch = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(
            session, _ctx(tenant_a, batch.id, 1), obj, [_diff("quote", "60.00", "65.00", "报价")]
        )
        await _record(
            session, _ctx(tenant_a, batch.id, 2), obj, [_diff("remark", "a", "b", "备注")]
        )
        warnings = await _record(
            session, _ctx(tenant_a, batch.id, 3), obj, [_diff("quote", "60.00", "70.00", "报价")]
        )
        assert warnings == ["第 3 行的报价与第 1 行不一致，冲突按第 1 行记"]
        [c] = await _all(session, obj)
        assert c.row_numbers == [1, 2, 3]
        assert {f["field"]: f["file"] for f in c.fields} == {"quote": "65.00", "remark": "b"}

    async def test_same_batch_kind_differs(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        batch = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(session, _ctx(tenant_a, batch.id, 1), obj, [_diff("quote", "60.00", "65.00")])
        warnings = await _record(
            session, _ctx(tenant_a, batch.id, 4), obj, [], kind="key", message="键冲突"
        )
        assert warnings == ["同一批次里该对象第 1、4 行的信息不一致"]
        [c] = await _all(session, obj)
        assert (c.kind, c.message, c.row_numbers) == ("fields", None, [1, 4])

    async def test_no_batch_id_never_merges(self, session: AsyncSession, tenant_a: Any) -> None:
        """ctx.batch_id 为 None（旧 upsert 路径）：已有冲突一律当别的批次。"""
        obj = uuid4()
        await _record(session, _ctx(tenant_a, None, 0), obj, [_diff("quote", "60.00", "65.00")])
        await _record(session, _ctx(tenant_a, None, 0), obj, [_diff("quote", "60.00", "66.00")])
        rows = await _all(session, obj)
        assert sorted(c.status for c in rows) == ["pending", "superseded"]
        pending = next(c for c in rows if c.status == "pending")
        assert pending.fields[0]["file"] == "66.00"

    async def test_label_clipped(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        batch = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(
            session, _ctx(tenant_a, batch.id, 1), obj, [_diff("remark", "a", "b")], label="名" * 400
        )
        [c] = await _all(session, obj)
        assert len(c.object_label) == 255
        assert c.object_label.endswith("…")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("tenant_ctx")
class TestTouch:
    async def test_other_batch_all_compared_supersedes(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        b1 = await import_batch_factory.batch(source=SOURCE)
        b2 = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(session, _ctx(tenant_a, b1.id, 1), obj, [_diff("quote", "60.00", "65.00")])
        assert await _touch(session, _ctx(tenant_a, b2.id, 1), obj, {"quote", "remark"}) == []
        [c] = await _all(session, obj)
        assert c.status == "superseded"
        assert c.resolved_at is not None
        assert c.resolved_by is None
        assert str(b2.id) in (c.resolution_note or "")

    async def test_other_batch_field_not_compared_keeps(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        """N5a：旧冲突里有字段本行没比较过 → 原样不动（不能当作已一致）。"""
        b1 = await import_batch_factory.batch(source=SOURCE)
        b2 = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(
            session,
            _ctx(tenant_a, b1.id, 1),
            obj,
            [_diff("quote", "60.00", "65.00"), _diff("remark", "a", "b")],
        )
        await _touch(session, _ctx(tenant_a, b2.id, 1), obj, {"remark"})
        [c] = await _all(session, obj)
        assert c.status == "pending"
        assert c.resolved_at is None

    async def test_key_conflict_without_fields_supersedes(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        b1 = await import_batch_factory.batch(source=SOURCE)
        b2 = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(session, _ctx(tenant_a, b1.id, 1), obj, [], kind="key", message="键冲突")
        await _touch(session, _ctx(tenant_a, b2.id, 1), obj, set())
        [c] = await _all(session, obj)
        assert c.status == "superseded"

    async def test_same_batch_untouched_with_warning(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        batch = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(
            session, _ctx(tenant_a, batch.id, 1), obj, [_diff("quote", "60.00", "65.00", "报价")]
        )
        warnings = await _touch(
            session, _ctx(tenant_a, batch.id, 5), obj, {"quote"}, {"quote": "60.00"}
        )
        assert warnings == ["第 5 行的报价与第 1 行不一致，冲突按第 1 行记"]
        [c] = await _all(session, obj)
        assert c.status == "pending"

    async def test_no_conflict_is_noop(self, session: AsyncSession, tenant_a: Any) -> None:
        assert await _touch(session, _ctx(tenant_a, uuid4(), 1), uuid4(), {"quote"}) == []


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("tenant_ctx")
class TestRecordOtherBatch:
    async def test_carries_uncompared_fields(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        """N5b：没比较过的旧字段用当前值重算后并入（已一致的丢掉，否则带 from_batch_id）。"""
        b1 = await import_batch_factory.batch(source=SOURCE)
        b2 = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(
            session,
            _ctx(tenant_a, b1.id, 1),
            obj,
            [
                _diff("follower_count", 1000, 2000),
                _diff("remark", "a", "B"),
                _diff("quote", "60.00", "65.00"),
            ],
        )
        await _record(
            session,
            _ctx(tenant_a, b2.id, 7),
            obj,
            [_diff("follower_count", 1000, 3000)],
            compared={"follower_count", "nickname"},
            current={"follower_count": 1000, "remark": "B", "quote": "61.00", "nickname": "小美"},
        )
        rows = await _all(session, obj)
        old = next(c for c in rows if c.status == "superseded")
        new = next(c for c in rows if c.status == "pending")
        assert old.superseded_by == new.id
        assert old.resolved_at is not None
        assert new.batch_id == b2.id
        assert new.row_numbers == [7]
        by_field = {f["field"]: f for f in new.fields}
        assert set(by_field) == {"follower_count", "quote"}  # remark 已与文件一致，不再列出
        assert by_field["follower_count"]["file"] == 3000
        assert "from_batch_id" not in by_field["follower_count"]
        assert by_field["quote"]["system"] == "61.00"  # 换成当前值
        assert by_field["quote"]["file"] == "65.00"  # 保留旧冲突的文件值
        assert by_field["quote"]["from_batch_id"] == str(b1.id)

    async def test_key_conflict_carries_fields(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        b1 = await import_batch_factory.batch(source=SOURCE)
        b2 = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(session, _ctx(tenant_a, b1.id, 1), obj, [_diff("quote", "60.00", "65.00")])
        await _record(
            session,
            _ctx(tenant_a, b2.id, 1),
            obj,
            [],
            kind="key",
            message="SKU 编码已属于别的款式",
            compared=set(),
            current={"quote": "60.00"},
        )
        rows = await _all(session, obj)
        new = next(c for c in rows if c.status == "pending")
        assert (new.kind, new.message) == ("key", "SKU 编码已属于别的款式")
        assert [(f["field"], f["from_batch_id"]) for f in new.fields] == [("quote", str(b1.id))]
        assert sum(1 for c in rows if c.status == "pending") == 1

    async def test_compared_and_now_equal_dropped(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        """C 里本行比较过且已一致的字段不再列出（只留本行仍不同的）。"""
        b1 = await import_batch_factory.batch(source=SOURCE)
        b2 = await import_batch_factory.batch(source=SOURCE)
        obj = uuid4()
        await _record(
            session,
            _ctx(tenant_a, b1.id, 1),
            obj,
            [_diff("quote", "60.00", "65.00"), _diff("remark", "a", "b")],
        )
        await _record(
            session,
            _ctx(tenant_a, b2.id, 1),
            obj,
            [_diff("remark", "a", "c")],
            compared={"quote", "remark"},
            current={"quote": "60.00", "remark": "a"},
        )
        new = next(c for c in await _all(session, obj) if c.status == "pending")
        assert [(f["field"], f["file"]) for f in new.fields] == [("remark", "c")]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("tenant_ctx")
class TestLockPendingAndIndex:
    async def test_lock_pending_scoped(
        self, session: AsyncSession, tenant_a: Any, import_batch_factory: Any
    ) -> None:
        batch = await import_batch_factory.batch(source=SOURCE)
        obj, other_obj = uuid4(), uuid4()
        await _record(session, _ctx(tenant_a, batch.id, 1), obj, [_diff("remark", "a", "b")])
        await _record(session, _ctx(tenant_a, batch.id, 1), other_obj, [_diff("remark", "a", "b")])
        # 别的来源、同一对象
        await _record(
            session,
            _ctx(tenant_a, batch.id, 1, source="manual_style_sku"),
            obj,
            [_diff("remark", "a", "b")],
        )
        rec = ConflictRecorder(session, _ctx(tenant_a, batch.id, 2))
        c = await rec.lock_pending("blogger", obj)
        assert c is not None
        assert (c.source, c.object_id, c.status) == (SOURCE, obj, "pending")
        assert await rec.lock_pending("style", obj) is None
        assert await rec.lock_pending("blogger", uuid4()) is None

        c.status = "kept"
        c.resolved_at = c.created_at
        await session.flush()
        assert await rec.lock_pending("blogger", obj) is None

    async def test_unique_pending_index(self, session: AsyncSession, tenant_a: Any) -> None:
        obj = uuid4()
        for _ in range(2):
            session.add(
                ImportConflict(
                    tenant_id=tenant_a.id,
                    source=SOURCE,
                    object_type="blogger",
                    object_id=obj,
                    object_key="k",
                    object_label="l",
                    kind="fields",
                    fields=[],
                    row_numbers=[1],
                )
            )
        with pytest.raises(IntegrityError) as exc_info:
            async with session.begin_nested():
                await session.flush()
        assert "uq_import_conflict_pending" in str(exc_info.value.orig)


class TestClipLabel:
    def test_clip_label(self) -> None:
        assert clip_label("短") == "短"
        assert clip_label("x" * 255) == "x" * 255
        clipped = clip_label("x" * 256)
        assert len(clipped) == 255
        assert clipped == "x" * 254 + "…"
