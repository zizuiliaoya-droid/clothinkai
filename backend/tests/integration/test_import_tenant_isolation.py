"""U06a 集成测试：runner per-row 租户隔离（NF-1 SET LOCAL）。

验证 runner 在 per-row 事务内 SET LOCAL app.tenant_id 后，adapter.upsert 写入的
业务记录 tenant_id 与 batch.tenant_id 一致（不串租）。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _run_import_batch


@pytest.mark.integration
@pytest.mark.asyncio
class TestRunnerTenantIsolation:
    async def test_upserted_records_carry_batch_tenant(
        self, engine: Any, monkeypatch
    ) -> None:
        """runner 写入的 brand 行 tenant_id == batch.tenant_id（NF-1 per-row SET LOCAL）。"""
        from tests.conftest import FakeImportAdapter

        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)

        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(FakeImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = (
            f"brand_code,brand_name\nBR{suffix}X,品牌X\nBR{suffix}Y,品牌Y\n"
        ).encode("utf-8")

        import app.core.attachment as attachment_mod

        monkeypatch.setattr(
            attachment_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        batch_id = uuid4()
        async with Maker() as seed:
            tenant_id = (
                await seed.execute(
                    text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1")
                )
            ).first()[0]
            await seed.execute(
                text(
                    "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                    "original_filename, file_r2_key, file_bucket, status, "
                    "total_rows, imported, failed, retry_count, created_at, updated_at) "
                    "VALUES (:id, :tid, 'fake_source', :h, 'd.csv', :key, "
                    "'private', 'processing', 0, 0, 0, 0, NOW(), NOW())"
                ),
                {
                    "id": batch_id,
                    "tid": tenant_id,
                    "h": suffix,
                    "key": f"imports/{tenant_id}/{batch_id}/d.csv",
                },
            )
            await seed.commit()

        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "completed"
            assert result["imported"] == 2

            async with Maker() as check:
                rows = (
                    await check.execute(
                        text(
                            "SELECT tenant_id FROM brand WHERE brand_code LIKE :p"
                        ),
                        {"p": f"BR{suffix}%"},
                    )
                ).fetchall()
                assert len(rows) == 2
                # NF-1：每行 tenant_id 都等于 batch.tenant_id（无串租）
                assert all(str(r[0]) == str(tenant_id) for r in rows)
        finally:
            ImportAdapterRegistry.clear()
            async with Maker() as cleanup:
                await cleanup.execute(
                    text("DELETE FROM import_job WHERE batch_id = :id"),
                    {"id": batch_id},
                )
                await cleanup.execute(
                    text("DELETE FROM import_batch WHERE id = :id"),
                    {"id": batch_id},
                )
                await cleanup.execute(
                    text("DELETE FROM brand WHERE brand_code LIKE :p"),
                    {"p": f"BR{suffix}%"},
                )
                await cleanup.commit()
