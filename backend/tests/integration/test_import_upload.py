"""U06a 集成测试：upload（DB 先行去重 NF-2 + 格式/大小校验 NF-6 + source 白名单）。

被测：ImportService.upload。run_import_batch.delay 被 monkeypatch 拦截（无 Celery）。
attachment 用 FakeAttachment（无真实 R2）。
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenancy import tenant_id_ctx
from app.modules.importer.exceptions import (
    ImportDuplicateFileError,
    ImportFormatUnsupportedError,
    ImportSourceUnknownError,
)
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.importer.service import ImportService


class _FakeAttachment:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.uploaded: list[str] = []

    def upload_bytes(self, data, *, bucket, key, content_type="application/octet-stream"):
        if self.fail:
            raise RuntimeError("R2 down")
        self.uploaded.append(key)
        return key


@pytest.fixture(autouse=True)
def _register_fake_adapter(monkeypatch):
    """注册 fake_source + 拦截 Celery delay。"""
    from tests.conftest import FakeImportAdapter

    ImportAdapterRegistry.clear()
    ImportAdapterRegistry.register(FakeImportAdapter())

    import app.tasks.import_tasks as tasks

    calls: list[str] = []
    monkeypatch.setattr(
        tasks.run_import_batch, "delay", lambda batch_id: calls.append(batch_id)
    )
    yield calls
    ImportAdapterRegistry.clear()


CSV_BYTES = b"brand_code,brand_name\nB1,name1\nB2,name2\n"


@pytest.mark.integration
@pytest.mark.asyncio
class TestUpload:
    async def _user(self, factory, tenant_a, pr_role):
        return await factory.user(tenant_a, roles=[pr_role])

    async def test_upload_creates_processing_batch(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await self._user(factory, tenant_a, pr_role)
            svc = ImportService(session, attachment_service=_FakeAttachment())
            batch = await svc.upload(
                content=CSV_BYTES,
                filename="data.csv",
                content_type="text/csv",
                source="fake_source",
                user=user,
            )
            assert batch.status == "processing"
            assert batch.source == "fake_source"
            assert batch.file_r2_key.startswith(f"imports/{tenant_a.id}/")
        finally:
            tenant_id_ctx.reset(token)

    async def test_upload_unknown_source_422(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await self._user(factory, tenant_a, pr_role)
            svc = ImportService(session, attachment_service=_FakeAttachment())
            with pytest.raises(ImportSourceUnknownError):
                await svc.upload(
                    content=CSV_BYTES,
                    filename="data.csv",
                    content_type="text/csv",
                    source="not_registered",
                    user=user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_upload_bad_format_422(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any
    ) -> None:
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await self._user(factory, tenant_a, pr_role)
            svc = ImportService(session, attachment_service=_FakeAttachment())
            with pytest.raises(ImportFormatUnsupportedError):
                await svc.upload(
                    content=b"...",
                    filename="data.txt",
                    content_type="text/plain",
                    source="fake_source",
                    user=user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_duplicate_file_409(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any
    ) -> None:
        """NF-2 DB 先行：同 (tenant, source, hash) 第二次 upload → 409。"""
        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await self._user(factory, tenant_a, pr_role)
            svc = ImportService(session, attachment_service=_FakeAttachment())
            await svc.upload(
                content=CSV_BYTES,
                filename="data.csv",
                content_type="text/csv",
                source="fake_source",
                user=user,
            )
            with pytest.raises(ImportDuplicateFileError):
                await svc.upload(
                    content=CSV_BYTES,  # 同内容 → 同 hash
                    filename="data2.csv",
                    content_type="text/csv",
                    source="fake_source",
                    user=user,
                )
        finally:
            tenant_id_ctx.reset(token)

    async def test_upload_r2_failure_compensates(
        self, session: AsyncSession, tenant_a: Any, factory: Any, pr_role: Any
    ) -> None:
        """NF-2 补偿：R2 写失败 → rollback batch（不留孤儿，可重新上传同文件）。"""
        from app.modules.importer.exceptions import ImportStorageError

        token = tenant_id_ctx.set(tenant_a.id)
        try:
            user = await self._user(factory, tenant_a, pr_role)
            svc = ImportService(session, attachment_service=_FakeAttachment(fail=True))
            with pytest.raises(ImportStorageError):
                await svc.upload(
                    content=CSV_BYTES,
                    filename="data.csv",
                    content_type="text/csv",
                    source="fake_source",
                    user=user,
                )
            # batch 已 rollback：同文件可再次上传（无孤儿 UNIQUE 残留）
            svc_ok = ImportService(session, attachment_service=_FakeAttachment())
            batch = await svc_ok.upload(
                content=CSV_BYTES,
                filename="data.csv",
                content_type="text/csv",
                source="fake_source",
                user=user,
            )
            assert batch.status == "processing"
        finally:
            tenant_id_ctx.reset(token)
