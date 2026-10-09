"""img8a：``StyleMainImageStore.replace`` 的「仅当为空才写」模式（导入内嵌图补主图用）。

导入补主图要求「已有主图的永远不覆盖」在写库那一刻成立：R2 写完、加锁重读后再判一次，
已有主图就回滚、补偿删除刚写的对象、返回 ``kept``。审计带 ``actor_type`` 与 ``audit_extra``。
默认参数（单张上传、批量上传）的行为不变。R2 一律用假 client，不向任何真实存储发请求。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import AuditLog
from app.modules.product.models import Style
from app.modules.product.style_image_service import StyleMainImageStore

PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 64


class _FakeS3:
    """只替掉要联网的调用；记录写入与删除的 key。"""

    def __init__(self) -> None:
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.fail_put = False

    def put_object(self, **kw: Any) -> dict[str, Any]:
        if self.fail_put:
            raise RuntimeError("fake r2 down")
        self.puts.append(kw["Key"])
        return {}

    def delete_object(self, **kw: Any) -> dict[str, Any]:
        self.deletes.append(kw["Key"])
        return {}


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> _FakeS3:
    from app.core import attachment as att_mod

    fake = _FakeS3()
    monkeypatch.setattr(att_mod.attachment_service, "_client", fake, raising=False)
    return fake


@pytest.fixture
def tenant_ctx(tenant_a: Any) -> Iterator[None]:
    from app.core.tenancy import tenant_id_ctx

    token = tenant_id_ctx.set(tenant_a.id)
    try:
        yield
    finally:
        tenant_id_ctx.reset(token)


async def _key(session: AsyncSession, style_id: UUID) -> str | None:
    return (
        await session.execute(
            select(Style.main_image_key)
            .where(Style.id == style_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _audits(session: AsyncSession, style_id: UUID) -> list[AuditLog]:
    rows = await session.execute(
        select(AuditLog).where(
            AuditLog.action == "style.main_image.update",
            AuditLog.resource_id == str(style_id),
        )
    )
    return list(rows.scalars().all())


def _import_kwargs(style_id: UUID, tenant_id: UUID, batch_id: str) -> dict[str, Any]:
    return {
        "style_id": style_id,
        "tenant_id": tenant_id,
        "user_id": None,
        "mime_type": "image/png",
        "data": PNG,
        "via": "import_embedded",
        "only_if_empty": True,
        "actor_type": "worker",
        "audit_extra": {"import_batch_id": batch_id, "row_number": 3},
    }


@pytest.mark.integration
@pytest.mark.asyncio
class TestOnlyIfEmpty:
    async def test_empty_style_created_with_audit(
        self,
        session: AsyncSession,
        product_factory: Any,
        tenant_a: Any,
        tenant_ctx: None,
        s3: _FakeS3,
    ) -> None:
        """没有主图 → created；审计 actor_type=worker、after 合并 audit_extra。"""
        style = await product_factory.style(style_code="IMP01")
        style_id = style.id
        batch_id = str(uuid4())
        result = await StyleMainImageStore(session).replace(
            **_import_kwargs(style_id, tenant_a.id, batch_id)
        )
        assert result.status == "created"
        assert s3.puts and (await _key(session, style_id)) == s3.puts[0]
        assert s3.deletes == []
        [audit] = await _audits(session, style_id)
        assert audit.actor_type == "worker"
        assert audit.user_id is None
        assert audit.tenant_id == tenant_a.id
        assert audit.before == {"main_image_changed": False}
        assert audit.after == {
            "main_image_changed": True,
            "via": "import_embedded",
            "filename": None,
            "import_batch_id": batch_id,
            "row_number": 3,
        }

    async def test_existing_image_kept(
        self,
        session: AsyncSession,
        product_factory: Any,
        tenant_a: Any,
        tenant_ctx: None,
        s3: _FakeS3,
    ) -> None:
        """已有主图 → kept：库不变、刚写的对象被删、无审计。"""
        old_key = f"{tenant_a.id}/styles/x/main/old_main.png"
        style = await product_factory.style(style_code="IMP02", main_image_key=old_key)
        style_id = style.id
        await session.commit()
        result = await StyleMainImageStore(session).replace(
            **_import_kwargs(style_id, tenant_a.id, str(uuid4()))
        )
        assert result.status == "kept"
        assert (await _key(session, style_id)) == old_key
        assert len(s3.puts) == 1
        assert s3.deletes == [s3.puts[0]]  # 只删新对象，旧主图不动
        assert await _audits(session, style_id) == []

    async def test_image_set_between_upload_and_lock_kept(
        self,
        session: AsyncSession,
        product_factory: Any,
        tenant_a: Any,
        tenant_ctx: None,
        s3: _FakeS3,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """R2 写完、加锁前有人传了主图 → 锁内复判不过：不覆盖、补偿删除新对象。"""
        style = await product_factory.style(style_code="IMP03")
        style_id = style.id
        await session.commit()
        other_key = f"{tenant_a.id}/styles/x/main/someone_main.png"
        store = StyleMainImageStore(session)
        real_lock = store._lock_style

        async def racing_lock(sid: UUID) -> Style | None:
            assert s3.puts, "应在 R2 写完之后才加锁"
            await session.execute(
                update(Style).where(Style.id == sid).values(main_image_key=other_key)
            )
            await session.commit()
            return await real_lock(sid)

        monkeypatch.setattr(store, "_lock_style", racing_lock)
        result = await store.replace(**_import_kwargs(style_id, tenant_a.id, str(uuid4())))
        assert result.status == "kept"
        assert (await _key(session, style_id)) == other_key
        assert s3.deletes == [s3.puts[0]]
        assert await _audits(session, style_id) == []

    async def test_storage_failure(
        self,
        session: AsyncSession,
        product_factory: Any,
        tenant_a: Any,
        tenant_ctx: None,
        s3: _FakeS3,
    ) -> None:
        """R2 写失败 → failed「存储失败」，库不变、无删除。"""
        style = await product_factory.style(style_code="IMP04")
        style_id = style.id
        s3.fail_put = True
        result = await StyleMainImageStore(session).replace(
            **_import_kwargs(style_id, tenant_a.id, str(uuid4()))
        )
        assert (result.status, result.reason) == ("failed", "存储失败")
        assert (await _key(session, style_id)) is None
        assert s3.deletes == []

    async def test_commit_failure_compensates(
        self,
        session: AsyncSession,
        product_factory: Any,
        tenant_a: Any,
        tenant_ctx: None,
        s3: _FakeS3,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """提交失败 → failed「保存失败」，补偿删除新对象，库不变、无审计。"""
        style = await product_factory.style(style_code="IMP05")
        style_id = style.id
        await session.commit()

        async def boom() -> None:
            raise RuntimeError("commit boom")

        monkeypatch.setattr(session, "commit", boom)
        result = await StyleMainImageStore(session).replace(
            **_import_kwargs(style_id, tenant_a.id, str(uuid4()))
        )
        monkeypatch.undo()
        assert (result.status, result.reason) == ("failed", "保存失败")
        assert s3.deletes == [s3.puts[0]]
        assert (await _key(session, style_id)) is None
        assert await _audits(session, style_id) == []

    async def test_default_still_replaces(
        self,
        session: AsyncSession,
        product_factory: Any,
        tenant_a: Any,
        tenant_ctx: None,
        s3: _FakeS3,
    ) -> None:
        """默认参数（单张 / 批量上传）照旧覆盖已有主图并删旧对象。"""
        old_key = f"{tenant_a.id}/styles/x/main/old_main.png"
        style = await product_factory.style(style_code="IMP06", main_image_key=old_key)
        style_id = style.id
        user_id = uuid4()
        result = await StyleMainImageStore(session).replace(
            style_id=style_id,
            tenant_id=tenant_a.id,
            user_id=user_id,
            mime_type="image/png",
            data=PNG,
        )
        assert result.status == "replaced"
        assert (await _key(session, style_id)) == s3.puts[0]
        assert s3.deletes == [old_key]
