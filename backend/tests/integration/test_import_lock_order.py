"""8a-6 N19（博主版）：导入处理已有对象的固定顺序（设计 §5.2、§13.1）。

锁冲突 → 加锁重读对象（populate_existing）→ 比较 → 写入 → touch / record。与裁决路径同为
「冲突 → 对象」，两者不会互相死锁；比较在对象行锁之内，加锁之前被别人改过的值不会被补空盖掉。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import tenant_id_ctx
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.conflict_appliers import BloggerApplier
from app.modules.importer.conflicts import ConflictRecorder
from app.modules.importer.outcome import BatchSeen, ImportRowContext, RowKind


def _parsed(xhs: str, **kw: Any) -> dict[str, Any]:
    adapter = BloggerImportAdapter()
    row = {"小红书ID": xhs, "昵称": "小美", **kw}
    return adapter.parse_row(row, None)


def _ctx(tenant_id: Any) -> ImportRowContext:
    return ImportRowContext(
        tenant_id=tenant_id,
        source="manual_blogger",
        batch_id=None,
        row_number=1,
        actor_id=None,
        batch_seen=BatchSeen(),
    )


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


@pytest.mark.integration
@pytest.mark.asyncio
class TestLockOrder:
    @pytest.mark.usefixtures("tenant_ctx")
    async def test_call_order(
        self,
        session: AsyncSession,
        tenant_a: Any,
        blogger_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """a）对已有博主补空 remark：lock_pending → load_for_update → apply → touch。"""
        b = await blogger_factory.blogger(nickname="小美", follower_count=None, remark=None)
        calls: list[str] = []

        def spy(cls: type, name: str) -> None:
            original = getattr(cls, name)

            async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                calls.append(name)
                return await original(self, *args, **kwargs)

            monkeypatch.setattr(cls, name, wrapper)

        spy(ConflictRecorder, "lock_pending")
        spy(BloggerApplier, "load_for_update")
        spy(BloggerApplier, "apply")
        spy(ConflictRecorder, "touch")
        spy(ConflictRecorder, "record")

        outcome = await BloggerImportAdapter().upsert_with_context(
            _parsed(b.xiaohongshu_id, 备注="A"), session=session, ctx=_ctx(tenant_a.id)
        )
        assert outcome.kind is RowKind.FILLED
        assert calls == ["lock_pending", "load_for_update", "apply", "touch"]

    async def test_concurrent_write_before_lock_not_overwritten(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """b）按键查到之后、加锁之前别人把 remark 改成 X 并提交 → 不补空、库里仍是 X、记冲突。"""
        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with Maker() as s:
            tenant_id = (
                await s.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
            ).scalar_one()
        xhs = f"xhsLOCK{uuid4().hex[:8]}"
        blogger_id = uuid4()
        async with Maker() as s:
            await s.execute(
                text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, platform, "
                    "category_tags, quality_tags, created_at, updated_at) VALUES "
                    "(:id, :tid, :x, '小美', '小红书', '[]'::jsonb, '[]'::jsonb, NOW(), NOW())"
                ),
                {"id": blogger_id, "tid": tenant_id, "x": xhs},
            )
            await s.commit()

        original = BloggerApplier.load_for_update

        async def concurrent_then_lock(self: Any, session: Any, object_id: Any, **kw: Any) -> Any:
            async with Maker() as other:
                await other.execute(
                    text("UPDATE blogger SET remark = 'X' WHERE id = :id"), {"id": object_id}
                )
                await other.commit()
            return await original(self, session, object_id, **kw)

        monkeypatch.setattr(BloggerApplier, "load_for_update", concurrent_then_lock)
        token = tenant_id_ctx.set(tenant_id)
        try:
            async with Maker() as s:
                await s.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
                )
                outcome = await BloggerImportAdapter().upsert_with_context(
                    _parsed(xhs, 备注="A"), session=s, ctx=_ctx(tenant_id)
                )
                await s.commit()
            assert outcome.kind is RowKind.CONFLICT
            async with Maker() as check:
                remark = (
                    await check.execute(
                        text("SELECT remark FROM blogger WHERE id = :id"), {"id": blogger_id}
                    )
                ).scalar_one()
                assert remark == "X"
                fields = (
                    await check.execute(
                        text(
                            "SELECT fields FROM import_conflict "
                            "WHERE object_id = :id AND status = 'pending'"
                        ),
                        {"id": blogger_id},
                    )
                ).scalar_one()
                assert [(f["field"], f["system"], f["file"]) for f in fields] == [
                    ("remark", "X", "A")
                ]
        finally:
            tenant_id_ctx.reset(token)
            async with Maker() as c:
                await c.execute(
                    text("DELETE FROM import_conflict WHERE object_id = :id"), {"id": blogger_id}
                )
                await c.execute(
                    text("DELETE FROM audit_log WHERE resource = 'blogger' AND resource_id = :r"),
                    {"r": str(blogger_id)},
                )
                await c.execute(text("DELETE FROM blogger WHERE id = :id"), {"id": blogger_id})
                await c.commit()
