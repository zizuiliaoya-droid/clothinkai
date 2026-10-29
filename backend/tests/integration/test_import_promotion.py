"""U06d 集成测试：PromotionImportAdapter 端到端（真实 adapter → runner → promotion 入库）。

复用 U06a/b/c test_import_runner 模式：monkeypatch AsyncSessionApp/Bypass → 测试 engine
+ mock get_object_bytes 注入样本 CSV + committed seed（style+blogger）+ finally 清理。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.modules.flow.line_events import PROMOTION_SOURCE_IMPORT
from app.modules.importer.adapters.promotion import PromotionImportAdapter
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _run_import_batch
from tests.conftest import purge_promotions
from tests.flow_event_helpers import assert_payload_registered


async def _seed(Maker, suffix: str, batch_id):
    """committed seed：tenant + style(ST<suffix>A) + blogger(xhs<suffix>A) + processing batch。"""
    async with Maker() as s:
        tenant_id = (
            await s.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
        ).first()[0]
        await s.execute(
            text(
                "INSERT INTO style (id, tenant_id, style_code, style_name, category, "
                "design_status, is_active, is_deleted, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :tid, :code, '连衣裙A', '连衣裙', '大货', "
                "true, false, NOW(), NOW())"
            ),
            {"tid": tenant_id, "code": f"ST{suffix}A"},
        )
        await s.execute(
            text(
                "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, platform, "
                "is_suspected_fake, is_active, is_deleted, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :tid, :xhs, '小美', '小红书', false, true, "
                "false, NOW(), NOW())"
            ),
            {"tid": tenant_id, "xhs": f"xhs{suffix}A"},
        )
        await s.execute(
            text(
                "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                "original_filename, file_r2_key, file_bucket, status, total_rows, "
                "imported, failed, retry_count, created_at, updated_at) "
                "VALUES (:id, :tid, 'manual_promotion', :h, 'promos.csv', :k, "
                "'private', 'processing', 0, 0, 0, 0, NOW(), NOW())"
            ),
            {
                "id": batch_id,
                "tid": tenant_id,
                "h": suffix,
                "k": f"imports/{tenant_id}/{batch_id}/promos.csv",
            },
        )
        await s.commit()
    return tenant_id


async def _cleanup(Maker, suffix: str, batch_id):
    async with Maker() as c:
        await c.execute(text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id})
        await c.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
        # promotion 引用 style/blogger，先删 promotion（连同子表）
        ids = (
            await c.execute(
                text("SELECT id FROM promotion WHERE style_code_snapshot = :code"),
                {"code": f"ST{suffix}A"},
            )
        ).scalars()
        await purge_promotions(c, ids)
        await c.execute(
            text(
                "DELETE FROM promotion_sequence WHERE tenant_id IN "
                "(SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1) "
                "AND date_key = '2026-06-01'"
            )
        )
        await c.execute(
            text("DELETE FROM blogger WHERE xiaohongshu_id = :xhs"),
            {"xhs": f"xhs{suffix}A"},
        )
        await c.execute(
            text("DELETE FROM style WHERE style_code = :code"),
            {"code": f"ST{suffix}A"},
        )
        await c.commit()


@pytest.mark.integration
@pytest.mark.asyncio
class TestPromotionImportEndToEnd:
    async def test_end_to_end_fk_resolution_and_sequence(self, engine: Any, monkeypatch) -> None:
        """2 成功（FK 解析 + internal_code 连续）+ 2 失败（缺 style / 缺 xhs）→ partial。"""
        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(PromotionImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = (
            "款式编码,小红书ID,报价金额,平台,合作日期,合作形式\n"
            f"ST{suffix}A,xhs{suffix}A,500.00,小红书,2026-06-01,单篇\n"
            f"ST{suffix}A,xhs{suffix}A,600,小红书,2026-06-01,单篇\n"
            f"ST{suffix}X,xhs{suffix}A,100,小红书,2026-06-01,单篇\n"
            f"ST{suffix}A,,100,小红书,2026-06-01,单篇\n"
        ).encode()

        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        batch_id = uuid4()
        tenant_id = await _seed(Maker, suffix, batch_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "partial"
            assert result["imported"] == 2
            assert result["failed"] == 2

            async with Maker() as check:
                promos = (
                    await check.execute(
                        text(
                            "SELECT internal_code, quote_amount, publish_status, "
                            "settlement_status, tenant_id FROM promotion "
                            "WHERE style_code_snapshot = :code ORDER BY internal_code"
                        ),
                        {"code": f"ST{suffix}A"},
                    )
                ).fetchall()
                assert len(promos) == 2
                categories = (
                    (
                        await check.execute(
                            text(
                                "SELECT content_category FROM promotion WHERE style_code_snapshot=:code"
                            ),
                            {"code": f"ST{suffix}A"},
                        )
                    )
                    .scalars()
                    .all()
                )
                assert categories == ["单篇", "单篇"]
                assert (
                    await check.scalar(
                        text(
                            "SELECT count(*) FROM line_event WHERE event_type='promotion.imported' AND payload->>'batch_id'=CAST(:batch AS text)"
                        ),
                        {"batch": str(batch_id)},
                    )
                    == 2
                )
                # internal_code 连续（同 cooperation_date，序号 +1）
                codes = [p[0] for p in promos]
                assert codes[0][:-4] == codes[1][:-4]  # 前缀+日期相同
                seqs = sorted(int(c[-4:]) for c in codes)
                assert seqs[1] == seqs[0] + 1
                # 3 状态初始态
                assert promos[0][2] == "未发布"
                assert promos[0][3] == "未核查"
                # 跨租户正确
                assert all(str(p[4]) == str(tenant_id) for p in promos)

                jobs = (
                    await check.execute(
                        text(
                            "SELECT row_number, status, error_detail FROM import_job "
                            "WHERE batch_id = :b ORDER BY row_number"
                        ),
                        {"b": batch_id},
                    )
                ).fetchall()
                assert [j[1] for j in jobs] == [
                    "success",
                    "success",
                    "failed",
                    "failed",
                ]
                assert "款式编码" in (jobs[2][2] or "")  # 缺 style
                assert "小红书ID" in (jobs[3][2] or "")  # 缺 xhs（validate）
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)

    async def test_missing_blogger_fails(self, engine: Any, monkeypatch) -> None:
        """style 存在但 blogger 不存在 → 行失败（FK 解析）。"""
        Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(PromotionImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = (
            "款式编码,小红书ID,报价金额,平台,合作日期\n"
            f"ST{suffix}A,xhs{suffix}MISSING,500,小红书,2026-06-01\n"
        ).encode()

        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        batch_id = uuid4()
        await _seed(Maker, suffix, batch_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "failed"
            assert result["failed"] == 1
            async with Maker() as check:
                err = (
                    await check.execute(
                        text("SELECT error_detail FROM import_job WHERE batch_id = :b"),
                        {"b": batch_id},
                    )
                ).scalar_one()
                assert "博主" in err and "不存在" in err
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)


def _use_test_engine(engine: Any, monkeypatch, csv_bytes: bytes):
    """runner 的两个 session 换成测试 engine、文件换成样本、只注册推广适配器。"""
    Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
    monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
    ImportAdapterRegistry.clear()
    ImportAdapterRegistry.register(PromotionImportAdapter())

    import app.core.attachment as att_mod

    monkeypatch.setattr(
        att_mod.attachment_service, "get_object_bytes", lambda bucket, key: csv_bytes
    )
    return Maker


@pytest.mark.integration
@pytest.mark.asyncio
class TestPromotionImportedEvent:
    """流程线 6.2 / G 块：runner 在 upsert 之后调 after_insert，每行一条 promotion.imported。"""

    async def test_each_inserted_row_writes_imported_event(self, engine: Any, monkeypatch) -> None:
        """3 行 1 行款式编码不存在 → 2 条 imported，batch_id / row_number / line_id 对得上（G8）。"""
        suffix = uuid4().hex[:8]
        csv_bytes = (
            "款式编码,小红书ID,报价金额,平台,合作日期\n"
            f"ST{suffix}A,xhs{suffix}A,500.00,小红书,2026-06-01\n"
            f"ST{suffix}X,xhs{suffix}A,100,小红书,2026-06-01\n"
            f"ST{suffix}A,xhs{suffix}A,600,小红书,2026-06-01\n"
        ).encode()
        Maker = _use_test_engine(engine, monkeypatch, csv_bytes)
        batch_id = uuid4()
        tenant_id = await _seed(Maker, suffix, batch_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["imported"] == 2
            assert result["failed"] == 1

            async with Maker() as check:
                jobs = (
                    await check.execute(
                        text(
                            "SELECT j.row_number, p.id, p.line_id FROM import_job j "
                            "JOIN promotion p ON p.id = j.target_resource_id "
                            "WHERE j.batch_id = :b AND j.status = 'success' ORDER BY j.row_number"
                        ),
                        {"b": batch_id},
                    )
                ).fetchall()
                assert len(jobs) == 2
                events = (
                    await check.execute(
                        text(
                            "SELECT e.promotion_id, e.line_id, e.negotiation_id, e.actor_id, "
                            "e.tenant_id, e.payload FROM line_event e "
                            "JOIN promotion p ON p.id = e.promotion_id "
                            "WHERE p.style_code_snapshot = :code "
                            "AND e.event_type = 'promotion.imported' "
                            "ORDER BY (e.payload->>'row_number')::int"
                        ),
                        {"code": f"ST{suffix}A"},
                    )
                ).fetchall()
            assert len(events) == 2
            for (row_number, promotion_id, line_id), event in zip(jobs, events, strict=True):
                assert event.promotion_id == promotion_id
                assert event.line_id == line_id
                assert event.negotiation_id is None
                assert str(event.tenant_id) == str(tenant_id)
                # 批次没有发起人 → actor NULL，payload 带 system（K4-2）
                assert event.actor_id is None
                assert event.payload == {
                    "source": PROMOTION_SOURCE_IMPORT,
                    "batch_id": str(batch_id),
                    "row_number": row_number,
                    "system": "import",
                }
                assert_payload_registered(event.payload)
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)

    async def test_batch_with_initiator_writes_actor(self, engine: Any, monkeypatch) -> None:
        """批次有发起人（生产路径：页面上传）→ imported 的 actor_id = 发起人，payload 不带 system。"""
        suffix = uuid4().hex[:8]
        csv_bytes = (
            "款式编码,小红书ID,报价金额,平台,合作日期\n"
            f"ST{suffix}A,xhs{suffix}A,500.00,小红书,2026-06-01\n"
        ).encode()
        Maker = _use_test_engine(engine, monkeypatch, csv_bytes)
        batch_id = uuid4()
        tenant_id = await _seed(Maker, suffix, batch_id)
        user_id = uuid4()
        async with Maker() as s:
            await s.execute(
                text(
                    'INSERT INTO "user" (id, tenant_id, username, password_hash, display_name, '
                    "status, password_must_change, failed_login_count, created_at, updated_at) "
                    "VALUES (:id, :tid, :u, 'x', '导入人', 'active', false, 0, NOW(), NOW())"
                ),
                {"id": user_id, "tid": tenant_id, "u": f"imppr_{suffix}"},
            )
            await s.execute(
                text("UPDATE import_batch SET created_by = :u WHERE id = :id"),
                {"u": user_id, "id": batch_id},
            )
            await s.commit()
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["imported"] == 1
            assert result["failed"] == 0

            async with Maker() as check:
                events = (
                    await check.execute(
                        text(
                            "SELECT e.actor_id, e.payload FROM line_event e "
                            "JOIN promotion p ON p.id = e.promotion_id "
                            "WHERE p.style_code_snapshot = :code "
                            "AND e.event_type = 'promotion.imported'"
                        ),
                        {"code": f"ST{suffix}A"},
                    )
                ).fetchall()
            assert len(events) == 1
            assert events[0].actor_id == user_id
            assert events[0].payload == {
                "source": PROMOTION_SOURCE_IMPORT,
                "batch_id": str(batch_id),
                "row_number": 1,
            }
            assert "system" not in events[0].payload
            assert_payload_registered(events[0].payload)
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)
            async with Maker() as c:
                await c.execute(text('DELETE FROM "user" WHERE id = :u'), {"u": user_id})
                await c.commit()

    async def test_hook_error_fails_the_row(self, engine: Any, monkeypatch) -> None:
        """钩子抛错 → 该行失败：推广单与 import_job(success) 都不落、失败明细有原因（I_ERR）。"""
        suffix = uuid4().hex[:8]
        csv_bytes = (
            "款式编码,小红书ID,报价金额,平台,合作日期\n"
            f"ST{suffix}A,xhs{suffix}A,500.00,小红书,2026-06-01\n"
            f"ST{suffix}A,xhs{suffix}A,600,小红书,2026-06-01\n"
        ).encode()
        Maker = _use_test_engine(engine, monkeypatch, csv_bytes)
        original = PromotionImportAdapter.after_insert
        calls: list[int] = []

        async def flaky_after_insert(self, session, rid, **kwargs):
            calls.append(kwargs["row_number"])
            if len(calls) == 2:
                raise RuntimeError("时间线写入失败")
            await original(self, session, rid, **kwargs)

        monkeypatch.setattr(PromotionImportAdapter, "after_insert", flaky_after_insert)
        batch_id = uuid4()
        await _seed(Maker, suffix, batch_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["imported"] == 1
            assert result["failed"] == 1

            async with Maker() as check:
                jobs = (
                    await check.execute(
                        text(
                            "SELECT row_number, status, error_detail, target_resource_id "
                            "FROM import_job WHERE batch_id = :b ORDER BY row_number"
                        ),
                        {"b": batch_id},
                    )
                ).fetchall()
                assert [j.row_number for j in jobs] == calls
                assert [j.status for j in jobs] == ["success", "failed"]
                assert "时间线写入失败" in (jobs[1].error_detail or "")
                assert jobs[1].target_resource_id is None
                promos = (
                    (
                        await check.execute(
                            text("SELECT id FROM promotion WHERE style_code_snapshot = :code"),
                            {"code": f"ST{suffix}A"},
                        )
                    )
                    .scalars()
                    .all()
                )
                assert promos == [jobs[0].target_resource_id]
                imported = (
                    await check.execute(
                        text(
                            "SELECT COUNT(*) FROM line_event WHERE promotion_id = :p "
                            "AND event_type = 'promotion.imported'"
                        ),
                        {"p": promos[0]},
                    )
                ).scalar_one()
                assert imported == 1
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)
