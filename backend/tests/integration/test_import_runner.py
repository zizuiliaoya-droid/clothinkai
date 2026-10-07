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
        raw = "\ufeffname\nv1\n".encode()
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
    async def test_partial_run_writes_jobs_and_records(self, engine: Any, monkeypatch) -> None:
        """FakeAdapter：3 行（1 行 _force_fail）→ batch=partial，2 success + 1 failed job。

        验证 NF-1 per-row 事务（成功行提交、失败行独立写）+ FB-A get_object_bytes。
        """
        from tests.conftest import FakeImportAdapter

        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
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
        ).encode()

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
                await seed.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
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
                        text("SELECT status, imported, failed FROM import_batch " "WHERE id = :id"),
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

    async def test_adapter_not_registered_marks_failed(self, engine: Any, monkeypatch) -> None:
        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()  # 无 adapter

        batch_id = uuid4()
        async with Maker() as seed:
            tenant_id = (
                await seed.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
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


# ---------------------------------------------------------------------------
# 8a-6：行结果三种新状态、批次七个计数、「没有失败行即 completed」（设计 §4.2）
# ---------------------------------------------------------------------------


class _KindAdapter:
    """按 CSV 的 kind 列返回行类别（实现 ContextAwareImportAdapter）；kind=fail 校验失败。"""

    source = "fake_kinds"
    target_table = "none"

    def parse_row(self, row: dict[str, Any], mapping: Any) -> dict[str, Any]:
        return dict(row)

    def validate(self, parsed: dict[str, Any]) -> list[str]:
        return ["强制失败"] if parsed.get("kind") == "fail" else []

    async def upsert(self, parsed: dict[str, Any], **kw: Any) -> tuple[Any, bool]:
        raise AssertionError("context-aware adapter 不应走旧 upsert")

    async def upsert_with_context(self, parsed: dict[str, Any], *, session: Any, ctx: Any) -> Any:
        from app.modules.importer.outcome import FilledRecord, RowKind, RowOutcome

        assert ctx.batch_seen is not None
        assert ctx.source == "fake_kinds"
        warnings = ["提示"] if parsed.get("warn") == "1" else []
        filled = [
            FilledRecord("blogger", f"对象{i}", ["remark"])
            for i in range(int(parsed.get("filled") or 0))
        ]
        return RowOutcome(
            resource_id=None, kind=RowKind(parsed["kind"]), warnings=warnings, filled=filled
        )


async def _seed_kinds_batch(Maker: Any, batch_id: Any, *, failed: int = 0) -> Any:
    async with Maker() as seed:
        tenant_id = (
            await seed.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
        ).first()[0]
        await seed.execute(
            text(
                "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                "original_filename, file_r2_key, file_bucket, status, "
                "total_rows, imported, failed, retry_count, created_at, updated_at) "
                "VALUES (:id, :tid, 'fake_kinds', :h, 'k.csv', :key, "
                "'private', 'processing', 0, 0, :failed, 0, NOW(), NOW())"
            ),
            {
                "id": batch_id,
                "tid": tenant_id,
                "h": uuid4().hex,
                "key": f"imports/{tenant_id}/{batch_id}/k.csv",
                "failed": failed,
            },
        )
        await seed.commit()
    return tenant_id


async def _batch_counts(Maker: Any, batch_id: Any) -> Any:
    async with Maker() as s:
        return (
            await s.execute(
                text(
                    "SELECT status, total_rows, imported, failed, filled, skipped, conflicted, "
                    "warning_count, filled_objects, error_summary FROM import_batch WHERE id = :id"
                ),
                {"id": batch_id},
            )
        ).first()


async def _cleanup_batch(Maker: Any, batch_id: Any) -> None:
    async with Maker() as c:
        await c.execute(text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id})
        await c.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
        await c.commit()


@pytest.fixture
def kinds_runner(engine: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
    monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
    saved = dict(ImportAdapterRegistry._adapters)
    ImportAdapterRegistry.clear()
    ImportAdapterRegistry.register(_KindAdapter())
    yield Maker
    ImportAdapterRegistry.clear()
    ImportAdapterRegistry._adapters.update(saved)


@pytest.mark.integration
@pytest.mark.asyncio
class TestRunnerRowKinds:
    async def test_no_failed_rows_is_completed(self, kinds_runner: Any, monkeypatch) -> None:
        """全是补空 / 跳过 / 冲突、没有失败行 → completed（旧逻辑 imported == 0 会判失败）。"""
        Maker = kinds_runner
        csv_bytes = b"kind,warn,filled\nfilled,0,1\nskipped,1,0\nconflict,1,2\ninserted,0,1\n"
        import app.core.attachment as attachment_mod

        monkeypatch.setattr(
            attachment_mod.attachment_service, "get_object_bytes", lambda b, k: csv_bytes
        )
        batch_id = uuid4()
        await _seed_kinds_batch(Maker, batch_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "completed"
            row = await _batch_counts(Maker, batch_id)
            assert tuple(row) == ("completed", 4, 1, 0, 1, 1, 1, 2, 4, None)
            async with Maker() as s:
                jobs = (
                    await s.execute(
                        text(
                            "SELECT status, notes FROM import_job WHERE batch_id = :b "
                            "ORDER BY row_number"
                        ),
                        {"b": batch_id},
                    )
                ).fetchall()
                nulls = (
                    await s.execute(
                        text(
                            "SELECT count(*) FROM import_job WHERE batch_id = :b AND notes IS NULL"
                        ),
                        {"b": batch_id},
                    )
                ).scalar_one()
            assert [j[0] for j in jobs] == ["filled", "skipped", "conflict", "success"]
            assert jobs[0][1] == {
                "warnings": [],
                "filled": [
                    {"object_type": "blogger", "object_label": "对象0", "fields": ["remark"]}
                ],
            }
            assert jobs[1][1] == {"warnings": ["提示"], "filled": []}
            assert nulls == 0
        finally:
            await _cleanup_batch(Maker, batch_id)

    async def test_conflict_and_failed_is_partial(self, kinds_runner: Any, monkeypatch) -> None:
        """有失败行、但不全是失败 → partial（即使一行都没有新增）。"""
        Maker = kinds_runner
        csv_bytes = b"kind\nconflict\nfail\nskipped\n"
        import app.core.attachment as attachment_mod

        monkeypatch.setattr(
            attachment_mod.attachment_service, "get_object_bytes", lambda b, k: csv_bytes
        )
        batch_id = uuid4()
        await _seed_kinds_batch(Maker, batch_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "partial"
            row = await _batch_counts(Maker, batch_id)
            assert tuple(row) == ("partial", 3, 0, 1, 0, 1, 1, 0, 0, "1 行失败")
        finally:
            await _cleanup_batch(Maker, batch_id)

    async def test_all_failed_is_failed(self, kinds_runner: Any, monkeypatch) -> None:
        Maker = kinds_runner
        csv_bytes = b"kind\nfail\nfail\n"
        import app.core.attachment as attachment_mod

        monkeypatch.setattr(
            attachment_mod.attachment_service, "get_object_bytes", lambda b, k: csv_bytes
        )
        batch_id = uuid4()
        await _seed_kinds_batch(Maker, batch_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "failed"
        finally:
            await _cleanup_batch(Maker, batch_id)

    async def test_only_failed_recounts_from_jobs(self, kinds_runner: Any) -> None:
        """只重跑失败行：七个计数按 import_job 重新数（失败行这次成了「重复已跳过」）。"""
        Maker = kinds_runner
        batch_id = uuid4()
        tenant_id = await _seed_kinds_batch(Maker, batch_id, failed=1)
        filled_two = (
            '{"warnings": [], "filled": [{"object_type": "blogger", "object_label": "a", '
            '"fields": ["remark"]}, {"object_type": "blogger", "object_label": "b", '
            '"fields": ["remark"]}]}'
        )
        jobs = [
            (1, "success", '{"kind": "inserted"}', None),
            (2, "filled", '{"kind": "filled"}', filled_two),
            (3, "conflict", '{"kind": "conflict"}', '{"warnings": ["w"], "filled": []}'),
            (4, "skipped", '{"kind": "skipped"}', None),
            (5, "failed", '{"kind": "skipped", "warn": "1"}', None),
        ]
        async with Maker() as s:
            for row_number, status, raw, notes in jobs:
                await s.execute(
                    text(
                        "INSERT INTO import_job (id, tenant_id, batch_id, row_number, status, "
                        "raw_data, notes, created_at, updated_at) VALUES (:id, :tid, :b, :rn, "
                        ":st, CAST(:raw AS jsonb), CAST(:notes AS jsonb), NOW(), NOW())"
                    ),
                    {
                        "id": uuid4(),
                        "tid": tenant_id,
                        "b": batch_id,
                        "rn": row_number,
                        "st": status,
                        "raw": raw,
                        "notes": notes,
                    },
                )
            await s.commit()
        try:
            result = await _run_import_batch(batch_id, only_failed=True)
            assert result["status"] == "completed"
            row = await _batch_counts(Maker, batch_id)
            # 总 5 行：success 1、filled 1、skipped 2、conflict 1；带提示 2 行；补空对象 2
            assert tuple(row) == ("completed", 5, 1, 0, 1, 2, 1, 2, 2, None)
            async with Maker() as s:
                attempts = (
                    await s.execute(
                        text(
                            "SELECT row_number, status, attempt_count FROM import_job "
                            "WHERE batch_id = :b ORDER BY row_number"
                        ),
                        {"b": batch_id},
                    )
                ).fetchall()
            # 只重跑了失败行（第 5 行 attempt_count + 1），其余行不动
            assert [(r[0], r[2]) for r in attempts] == [(1, 1), (2, 1), (3, 1), (4, 1), (5, 2)]
            assert attempts[4][1] == "skipped"
        finally:
            await _cleanup_batch(Maker, batch_id)
