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
from app.modules.importer.adapters.promotion import PromotionImportAdapter
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _run_import_batch


async def _seed(Maker, suffix: str, batch_id):
    """committed seed：tenant + style(ST<suffix>A) + blogger(xhs<suffix>A) + processing batch。"""
    async with Maker() as s:
        tenant_id = (
            await s.execute(
                text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1")
            )
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
        await c.execute(
            text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id}
        )
        await c.execute(
            text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id}
        )
        # promotion 引用 style/blogger，先删 promotion
        await c.execute(
            text(
                "DELETE FROM promotion WHERE style_code_snapshot = :code"
            ),
            {"code": f"ST{suffix}A"},
        )
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
    async def test_end_to_end_fk_resolution_and_sequence(
        self, engine: Any, monkeypatch
    ) -> None:
        """2 成功（FK 解析 + internal_code 连续）+ 2 失败（缺 style / 缺 xhs）→ partial。"""
        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(PromotionImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = (
            "款式编码,小红书ID,报价金额,平台,合作日期\n"
            f"ST{suffix}A,xhs{suffix}A,500.00,小红书,2026-06-01\n"
            f"ST{suffix}A,xhs{suffix}A,600,小红书,2026-06-01\n"
            f"ST{suffix}X,xhs{suffix}A,100,小红书,2026-06-01\n"
            f"ST{suffix}A,,100,小红书,2026-06-01\n"
        ).encode("utf-8")

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
        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(PromotionImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = (
            "款式编码,小红书ID,报价金额,平台,合作日期\n"
            f"ST{suffix}A,xhs{suffix}MISSING,500,小红书,2026-06-01\n"
        ).encode("utf-8")

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
                        text(
                            "SELECT error_detail FROM import_job WHERE batch_id = :b"
                        ),
                        {"b": batch_id},
                    )
                ).scalar_one()
                assert "博主" in err and "不存在" in err
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)
