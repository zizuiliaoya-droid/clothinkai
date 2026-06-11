"""U06a 集成测试：run_import_batch runner（解析 + per-row upsert + 汇总 + FB-A get_object_bytes）。

两部分：
- 纯函数（CI 安全，无 DB）：_parse_rows CSV/XLSX + _sanitize
- 端到端（committed 数据 + 清理，仿 concurrency 测试）：runner 完整流程，
  monkeypatch AsyncSessionApp/Bypass 指向测试 engine + mock get_object_bytes（FB-A）。
"""

from __future__ import annotations

import io
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _parse_rows, _run_import_batch, _sanitize


# ---------------------------------------------------------------------------
# 纯函数（CI 安全）
# ---------------------------------------------------------------------------


class TestParseRows:
    def test_parse_csv(self):
        raw = b"name,age\nAlice,30\nBob,25\n"
        rows = _parse_rows(raw, "data.csv")
        assert rows == [
            (1, {"name": "Alice", "age": "30"}),
            (2, {"name": "Bob", "age": "25"}),
        ]

    def test_parse_csv_with_bom(self):
        raw = "\ufeffname\nv1\n".encode("utf-8")
        rows = _parse_rows(raw, "x.csv")
        assert rows == [(1, {"name": "v1"})]

    def test_parse_xlsx(self):
        openpyxl = pytest.importorskip("openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["name", "qty"])
        ws.append(["杯子", 5])
        ws.append(["碗", 8])
        buf = io.BytesIO()
        wb.save(buf)
        rows = _parse_rows(buf.getvalue(), "data.xlsx")
        assert rows[0] == (1, {"name": "杯子", "qty": "5"})
        assert rows[1] == (2, {"name": "碗", "qty": "8"})

    def test_parse_unsupported_ext_raises(self):
        with pytest.raises(ValueError):
            _parse_rows(b"x", "data.txt")


class TestSanitize:
    def test_sanitize_truncates(self):
        out = _sanitize(ValueError("x" * 5000))
        assert out.startswith("ValueError: ")
        assert len(out) <= 1000


# ---------------------------------------------------------------------------
# 端到端（committed 数据 + 清理）
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestRunnerEndToEnd:
    async def test_partial_run_writes_jobs_and_records(
        self, engine: Any, monkeypatch
    ) -> None:
        """FakeAdapter：3 行（1 行 _force_fail）→ batch=partial，2 success + 1 failed job。

        验证 NF-1 per-row 事务（成功行提交、失败行独立写）+ FB-A get_object_bytes。
        """
        from tests.conftest import FakeImportAdapter

        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        # runner 的双 session 都指向测试 engine
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)

        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(FakeImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = (
            "brand_code,brand_name,_force_fail\n"
            f"BR{suffix}A,品牌A,0\n"
            f"BR{suffix}B,品牌B,0\n"
            f"BR{suffix}C,品牌C,1\n"
        ).encode("utf-8")

        # FB-A：mock U01 R2 helper（不碰 attachment ORM）
        import app.core.attachment as attachment_mod

        monkeypatch.setattr(
            attachment_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        batch_id = uuid4()
        # seed committed：默认 tenant + processing batch
        async with Maker() as seed:
            tenant_row = (
                await seed.execute(
                    text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1")
                )
            ).first()
            assert tenant_row is not None, "默认 tenant 缺失（003 seed 未跑）"
            tenant_id = tenant_row[0]
            await seed.execute(
                text(
                    "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                    "original_filename, file_r2_key, file_bucket, status, "
                    "total_rows, imported, failed, retry_count, created_at, updated_at) "
                    "VALUES (:id, :tid, 'fake_source', :h, 'data.csv', :key, "
                    "'private', 'processing', 0, 0, 0, 0, NOW(), NOW())"
                ),
                {
                    "id": batch_id,
                    "tid": tenant_id,
                    "h": suffix,
                    "key": f"imports/{tenant_id}/{batch_id}/data.csv",
                },
            )
            await seed.commit()

        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "partial"
            assert result["imported"] == 2
            assert result["failed"] == 1

            async with Maker() as check:
                jobs = (
                    await check.execute(
                        text(
                            "SELECT status FROM import_job WHERE batch_id = :bid "
                            "ORDER BY row_number"
                        ),
                        {"bid": batch_id},
                    )
                ).fetchall()
                statuses = [r[0] for r in jobs]
                assert statuses == ["success", "success", "failed"]

                batch_row = (
                    await check.execute(
                        text(
                            "SELECT status, imported, failed FROM import_batch "
                            "WHERE id = :id"
                        ),
                        {"id": batch_id},
                    )
                ).first()
                assert batch_row[0] == "partial"
                assert batch_row[1] == 2
                assert batch_row[2] == 1
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

    async def test_adapter_not_registered_marks_failed(
        self, engine: Any, monkeypatch
    ) -> None:
        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()  # 无 adapter

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
                    "VALUES (:id, :tid, 'ghost_source', :h, 'd.csv', :key, "
                    "'private', 'processing', 0, 0, 0, 0, NOW(), NOW())"
                ),
                {
                    "id": batch_id,
                    "tid": tenant_id,
                    "h": uuid4().hex[:8],
                    "key": f"imports/{tenant_id}/{batch_id}/d.csv",
                },
            )
            await seed.commit()
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "failed"
            assert result["reason"] == "adapter_not_registered"
        finally:
            async with Maker() as cleanup:
                await cleanup.execute(
                    text("DELETE FROM import_batch WHERE id = :id"),
                    {"id": batch_id},
                )
                await cleanup.commit()
