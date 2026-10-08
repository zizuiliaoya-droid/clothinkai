"""8a-6 N19（博主版）：导入处理已有对象的固定顺序（设计 §5.2、§13.1）。

锁冲突 → 加锁重读对象（populate_existing）→ 比较 → 写入 → touch / record。与裁决路径同为
「冲突 → 对象」，两者不会互相死锁；比较在对象行锁之内，加锁之前被别人改过的值不会被补空盖掉。
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.tenancy import tenant_id_ctx
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.adapters.style_sku import StyleSkuImportAdapter
from app.modules.importer.conflict_appliers import BloggerApplier, StyleApplier
from app.modules.importer.conflicts import ConflictRecorder
from app.modules.importer.outcome import BatchSeen, ImportRowContext, RowKind
from app.modules.product.models import Style


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


# ---------------------------------------------------------------------------
# 款式版（8a-4，N19）
# ---------------------------------------------------------------------------

_URL_A = "https://img.example.invalid/a.jpg"


def _style_parsed(style_code: str, sku_code: str, **kw: Any) -> dict[str, Any]:
    row = {
        "款式编码": style_code,
        "商品编码": sku_code,
        "商品名称": "裙",
        "颜色": "红",
        "规格": "M",
    }
    return StyleSkuImportAdapter().parse_row({**row, **kw}, None)


def _style_ctx(tenant_id: Any) -> ImportRowContext:
    return ImportRowContext(
        tenant_id=tenant_id,
        source="manual_style_sku",
        batch_id=None,
        row_number=1,
        actor_id=None,
        batch_seen=BatchSeen(),
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestStyleLockOrder:
    @pytest.mark.usefixtures("tenant_ctx")
    async def test_call_order(
        self,
        session: AsyncSession,
        tenant_a: Any,
        product_factory: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """a）对已有款式补空外部链接：lock_pending → load_for_update → apply → touch。"""
        style = await product_factory.style()
        sku = await product_factory.sku(style, color="红", size="M")
        calls: list[str] = []

        def spy(cls: type, name: str, tag: Callable[[tuple[Any, ...]], str]) -> None:
            original = getattr(cls, name)

            async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
                calls.append(f"{name}:{tag(args)}")
                return await original(self, *args, **kwargs)

            monkeypatch.setattr(cls, name, wrapper)

        spy(ConflictRecorder, "lock_pending", lambda a: str(a[0]))
        spy(ConflictRecorder, "touch", lambda a: str(a[1]))
        spy(ConflictRecorder, "record", lambda a: str(a[1]))
        spy(StyleApplier, "load_for_update", lambda a: "style")
        spy(StyleApplier, "apply", lambda a: "style")

        outcome = await StyleSkuImportAdapter().upsert_with_context(
            _style_parsed(style.style_code, sku.sku_code, 图片=_URL_A),
            session=session,
            ctx=_style_ctx(tenant_a.id),
        )
        assert outcome.kind is RowKind.INSERTED  # 款式补空 + 顺手新建单品商品
        assert calls[:4] == [
            "lock_pending:style",
            "load_for_update:style",
            "apply:style",
            "touch:style",
        ]
        assert (await session.get(Style, style.id)).external_image_url == _URL_A  # type: ignore[union-attr]

    async def test_concurrent_write_before_lock_not_overwritten(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """b）load_for_update 之前另一会话把外部链接改成 X 并提交 → 不补空、仍是 X、记冲突（X / A）。"""
        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with Maker() as s:
            tenant_id = (
                await s.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
            ).scalar_one()
        tag = uuid4().hex[:8]
        style_code, sku_code = f"SLOCK{tag}", f"KLOCK{tag}"
        style_id = uuid4()
        async with Maker() as s:
            await s.execute(
                text(
                    "INSERT INTO style (id, tenant_id, style_code, style_name, tags, tag_color, "
                    "design_status, is_active, is_deleted, created_at, updated_at) VALUES "
                    "(:id, :tid, :c, '裙', '[]'::jsonb, '[]'::jsonb, '大货', true, false, "
                    "NOW(), NOW())"
                ),
                {"id": style_id, "tid": tenant_id, "c": style_code},
            )
            await s.commit()

        original = StyleApplier.load_for_update

        async def concurrent_then_lock(self: Any, session: Any, object_id: Any, **kw: Any) -> Any:
            async with Maker() as other:
                await other.execute(
                    text("UPDATE style SET external_image_url = 'X' WHERE id = :id"),
                    {"id": object_id},
                )
                await other.commit()
            return await original(self, session, object_id, **kw)

        monkeypatch.setattr(StyleApplier, "load_for_update", concurrent_then_lock)
        token = tenant_id_ctx.set(tenant_id)
        try:
            async with Maker() as s:
                await s.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
                )
                outcome = await StyleSkuImportAdapter().upsert_with_context(
                    _style_parsed(style_code, sku_code, 图片=_URL_A),
                    session=s,
                    ctx=_style_ctx(tenant_id),
                )
                await s.commit()
            assert outcome.kind is RowKind.CONFLICT
            async with Maker() as check:
                url = (
                    await check.execute(
                        text("SELECT external_image_url FROM style WHERE id = :id"),
                        {"id": style_id},
                    )
                ).scalar_one()
                assert url == "X"
                fields = (
                    await check.execute(
                        text(
                            "SELECT fields FROM import_conflict "
                            "WHERE object_id = :id AND status = 'pending'"
                        ),
                        {"id": style_id},
                    )
                ).scalar_one()
                assert [(f["field"], f["system"], f["file"]) for f in fields] == [
                    ("external_image_url", "X", _URL_A)
                ]
        finally:
            tenant_id_ctx.reset(token)
            async with Maker() as c:
                await c.execute(
                    text("DELETE FROM import_conflict WHERE object_id = :id"), {"id": style_id}
                )
                await c.execute(
                    text(
                        "DELETE FROM audit_log WHERE resource_id IN (SELECT id::text FROM "
                        "goods_main WHERE goods_code = :c)"
                    ),
                    {"c": style_code},
                )
                await c.execute(
                    text(
                        "DELETE FROM goods_style_item WHERE goods_main_id IN "
                        "(SELECT id FROM goods_main WHERE goods_code = :c)"
                    ),
                    {"c": style_code},
                )
                await c.execute(
                    text("DELETE FROM goods_main WHERE goods_code = :c"), {"c": style_code}
                )
                await c.execute(text("DELETE FROM sku WHERE sku_code = :c"), {"c": sku_code})
                await c.execute(text("DELETE FROM style WHERE id = :id"), {"id": style_id})
                await c.commit()
