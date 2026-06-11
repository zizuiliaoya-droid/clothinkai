"""U06b 集成测试：StyleSkuImportAdapter 端到端（真实 adapter → runner → style/sku 入库）。

复用 U06a test_import_runner 模式：monkeypatch AsyncSessionApp/Bypass → 测试 engine
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
from app.modules.importer.adapters.style_sku import StyleSkuImportAdapter
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _run_import_batch


def _csv(suffix: str) -> bytes:
    """样本 CSV：新建 / 复用 style / 缺 SKU编码 失败。"""
    return (
        "款式编码,款式名称,类目,品牌编码,SKU编码,颜色,尺码,成本价,货源类型\n"
        f"ST{suffix}A,连衣裙A,连衣裙,,SK{suffix}A-红-M,红,M,\"1,299.00\",自产\n"
        f"ST{suffix}A,连衣裙A,连衣裙,,SK{suffix}A-红-L,红,L,39.90,自产\n"
        f"ST{suffix}B,上衣B,上衣,,,蓝,M,20.00,采购\n"
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
                "VALUES (:id, :tid, 'manual_style_sku', :h, 'styles.csv', :k, "
                "'private', 'processing', 0, 0, 0, 0, NOW(), NOW())"
            ),
            {
                "id": batch_id,
                "tid": tenant_id,
                "h": suffix,
                "k": f"imports/{tenant_id}/{batch_id}/styles.csv",
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
            text(
                "DELETE FROM sku WHERE sku_code LIKE :p"
            ),
            {"p": f"SK{suffix}%"},
        )
        await c.execute(
            text("DELETE FROM style WHERE style_code LIKE :p"),
            {"p": f"ST{suffix}%"},
        )
        await c.commit()


@pytest.mark.integration
@pytest.mark.asyncio
class TestStyleSkuImportEndToEnd:
    async def test_end_to_end_partial(self, engine: Any, monkeypatch) -> None:
        """2 成功（新建 + 复用 style）+ 1 失败（缺 SKU编码）→ partial。"""
        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(StyleSkuImportAdapter())

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
            assert result["imported"] == 2
            assert result["failed"] == 1

            async with Maker() as check:
                # style：仅 1 个 ST..A（复用），ST..B 因 sku 缺失整行回滚不应残留
                styles = (
                    await check.execute(
                        text(
                            "SELECT style_code FROM style WHERE style_code LIKE :p "
                            "ORDER BY style_code"
                        ),
                        {"p": f"ST{suffix}%"},
                    )
                ).fetchall()
                codes = [r[0] for r in styles]
                assert f"ST{suffix}A" in codes
                assert f"ST{suffix}B" not in codes  # 整行回滚（FB-C per-row 原子）

                # sku：2 个成功 + Decimal 精度（按 sku_code 排序：-红-L 在 -红-M 前）
                skus = (
                    await check.execute(
                        text(
                            "SELECT sku_code, cost_price, tenant_id FROM sku "
                            "WHERE sku_code LIKE :p ORDER BY sku_code"
                        ),
                        {"p": f"SK{suffix}%"},
                    )
                ).fetchall()
                assert len(skus) == 2
                # 两行成本价：1,299.00（千分位解析）+ 39.90，与顺序无关
                cost_prices = {r[1] for r in skus}
                assert cost_prices == {Decimal("1299.00"), Decimal("39.90")}
                assert all(str(r[2]) == str(tenant_id) for r in skus)  # 跨租户正确

                # import_job：第 3 行 failed + error_detail
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
                assert "SKU编码" in (jobs[2][2] or "")
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)

    async def test_retry_only_failed_idempotent(
        self, engine: Any, monkeypatch
    ) -> None:
        """retry only_failed：缺字段行重跑仍 failed，不产生重复 sku（幂等）。"""
        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(StyleSkuImportAdapter())

        suffix = uuid4().hex[:8]
        csv_bytes = _csv(suffix)
        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        batch_id = uuid4()
        await _seed_batch(Maker, suffix, batch_id)
        try:
            await _run_import_batch(batch_id, only_failed=False)
            # 模拟 retry 端点的 claim_for_retry：先把 batch 置回 processing
            # （真实流程由 service.retry → claim_for_retry 原子完成，NF-3）
            async with Maker() as s:
                await s.execute(
                    text(
                        "UPDATE import_batch SET status='processing', "
                        "retry_count=retry_count+1 WHERE id=:id"
                    ),
                    {"id": batch_id},
                )
                await s.commit()
            # 重跑 only_failed（第 3 行仍缺 sku_code）
            result2 = await _run_import_batch(batch_id, only_failed=True)
            assert result2["status"] == "partial"

            async with Maker() as check:
                # sku 仍是 2 个（重跑未重复创建）
                count = (
                    await check.execute(
                        text(
                            "SELECT count(*) FROM sku WHERE sku_code LIKE :p"
                        ),
                        {"p": f"SK{suffix}%"},
                    )
                ).scalar_one()
                assert count == 2
                # 失败行 attempt_count 递增（原地更新）
                attempt = (
                    await check.execute(
                        text(
                            "SELECT attempt_count FROM import_job WHERE batch_id = :b "
                            "AND status = 'failed'"
                        ),
                        {"b": batch_id},
                    )
                ).scalar_one()
                assert attempt >= 2
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)
