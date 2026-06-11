"""U06c 集成测试：BloggerImportAdapter 端到端（真实 adapter → runner → blogger 入库）。

复用 U06a/U06b test_import_runner 模式：monkeypatch AsyncSessionApp/Bypass → 测试 engine
+ mock attachment_service.get_object_bytes 注入样本 CSV + committed 数据 + finally 清理。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.modules.importer.adapters.blogger import BloggerImportAdapter
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _run_import_batch


def _csv(suffix: str) -> bytes:
    """样本 CSV：新建 / 同 ID UPDATE / 缺 ID 失败。"""
    return (
        "小红书ID,昵称,粉丝数,报价,类目标签,质量标签\n"
        f"xhs{suffix}A,小美,\"12,500\",500.00,美妆;护肤,优质\n"
        f"xhs{suffix}A,小美改名,13000,600,美妆,\n"
        f",无ID博主,1000,100,,\n"
    ).encode("utf-8")


async def _seed_batch(Maker, suffix: str, batch_id) -> Any:
    async with Maker() as seed:
        tenant_id = (
            await seed.execute(
                text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1")
            )
        ).first()[0]
        await seed.execute(
            text(
                "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                "original_filename, file_r2_key, file_bucket, status, total_rows, "
                "imported, failed, retry_count, created_at, updated_at) "
                "VALUES (:id, :tid, 'manual_blogger', :h, 'bloggers.csv', :k, "
                "'private', 'processing', 0, 0, 0, 0, NOW(), NOW())"
            ),
            {
                "id": batch_id,
                "tid": tenant_id,
                "h": suffix,
                "k": f"imports/{tenant_id}/{batch_id}/bloggers.csv",
            },
        )
        await seed.commit()
    return tenant_id


async def _cleanup(Maker, suffix: str, batch_id) -> None:
    async with Maker() as c:
        await c.execute(
            text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id}
        )
        await c.execute(
            text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id}
        )
        await c.execute(
            text("DELETE FROM blogger WHERE xiaohongshu_id LIKE :p"),
            {"p": f"xhs{suffix}%"},
        )
        await c.commit()


@pytest.mark.integration
@pytest.mark.asyncio
class TestBloggerImportEndToEnd:
    async def test_end_to_end_partial_and_update(
        self, engine: Any, monkeypatch
    ) -> None:
        """新建 + 同 ID UPDATE + 缺 ID failed → partial；标签 JSONB + int + Decimal。"""
        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(BloggerImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = _csv(suffix)
        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        batch_id = uuid4()
        tenant_id = await _seed_batch(Maker, suffix, batch_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "partial"
            # 行 1 INSERT + 行 2 UPDATE 同 ID（都 success）+ 行 3 缺 ID failed
            assert result["imported"] == 2
            assert result["failed"] == 1

            async with Maker() as check:
                # 同 xiaohongshu_id 仅 1 条 blogger（UPDATE 不新建）
                bloggers = (
                    await check.execute(
                        text(
                            "SELECT xiaohongshu_id, nickname, follower_count, quote, "
                            "category_tags, tenant_id FROM blogger "
                            "WHERE xiaohongshu_id LIKE :p"
                        ),
                        {"p": f"xhs{suffix}%"},
                    )
                ).fetchall()
                assert len(bloggers) == 1
                blg = bloggers[0]
                # 第 2 行 UPDATE 生效（昵称改名 + follower 13000 + quote 600）
                assert blg[1] == "小美改名"
                assert blg[2] == 13000
                assert blg[3] == Decimal("600")
                # 标签 JSONB 数组
                assert blg[4] == ["美妆"]
                # 跨租户正确
                assert str(blg[5]) == str(tenant_id)

                jobs = (
                    await check.execute(
                        text(
                            "SELECT row_number, status, error_detail FROM import_job "
                            "WHERE batch_id = :b ORDER BY row_number"
                        ),
                        {"b": batch_id},
                    )
                ).fetchall()
                assert [j[1] for j in jobs] == ["success", "success", "failed"]
                assert "小红书ID" in (jobs[2][2] or "")
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)

    async def test_tags_jsonb_first_row(self, engine: Any, monkeypatch) -> None:
        """验证多标签解析为 JSONB 数组（单行隔离，不被第 2 行 UPDATE 覆盖）。"""
        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(BloggerImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = (
            "小红书ID,昵称,类目标签\n"
            f"xhs{suffix}T,标签博主,\"美妆;护肤,穿搭\"\n"
        ).encode("utf-8")
        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        batch_id = uuid4()
        await _seed_batch(Maker, suffix, batch_id)
        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "completed"
            async with Maker() as check:
                tags = (
                    await check.execute(
                        text(
                            "SELECT category_tags FROM blogger "
                            "WHERE xiaohongshu_id = :x"
                        ),
                        {"x": f"xhs{suffix}T"},
                    )
                ).scalar_one()
                assert tags == ["美妆", "护肤", "穿搭"]
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)
