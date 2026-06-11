"""U06e 集成测试：SettlementImportAdapter 端到端（真实 adapter → runner → settlement 入库）。

复用 U06d test_import_promotion 模式：monkeypatch AsyncSessionApp/Bypass → 测试 engine
+ mock get_object_bytes 注入样本 CSV + committed seed（style+blogger+2 promotion+1 已有 settlement）
+ finally 清理。

校验 U06e 特性：
- INSERT-only + promotion 派生（blogger_id/style_id/pr_id 从 promotion）
- settlement_no 生成（FB2 序列 + format）
- 合成 request_event_id（非空唯一）
- UNIQUE(promotion_id) 冲突 → failed（FB3 不覆盖）
- promotion 缺失 → failed
- 不触发事件（event_capture 空）
- 跨租户 tenant_id 正确
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.modules.importer.adapters.settlement import SettlementImportAdapter
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _run_import_batch


async def _seed(Maker, suffix: str, batch_id):
    """committed seed：tenant + style + blogger + 2 promotion（1 待结算 / 1 已有结算）
    + 1 已有 settlement（模拟事件创建）+ processing batch。

    返回 (tenant_id, promo1_code, promo2_code)。
    """
    promo1_code = f"PR{suffix}1"
    promo2_code = f"PR{suffix}2"
    async with Maker() as s:
        tenant_id = (
            await s.execute(
                text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1")
            )
        ).first()[0]
        style_id = (
            await s.execute(
                text(
                    "INSERT INTO style (id, tenant_id, style_code, style_name, category, "
                    "design_status, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :tid, :code, '连衣裙A', '连衣裙', '大货', "
                    "true, false, NOW(), NOW()) RETURNING id"
                ),
                {"tid": tenant_id, "code": f"ST{suffix}A"},
            )
        ).scalar_one()
        blogger_id = (
            await s.execute(
                text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, platform, "
                    "is_suspected_fake, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (gen_random_uuid(), :tid, :xhs, '小美', '小红书', false, true, "
                    "false, NOW(), NOW()) RETURNING id"
                ),
                {"tid": tenant_id, "xhs": f"xhs{suffix}A"},
            )
        ).scalar_one()
        promo_ids = {}
        for code in (promo1_code, promo2_code):
            pid = (
                await s.execute(
                    text(
                        "INSERT INTO promotion (id, tenant_id, style_id, blogger_id, "
                        "internal_code, style_code_snapshot, style_short_name_snapshot, "
                        "quote_amount, platform, cooperation_date, is_active, "
                        "created_at, updated_at) "
                        "VALUES (gen_random_uuid(), :tid, :sid, :bid, :code, :scode, "
                        "'连衣裙A', 500.00, '小红书', '2026-06-01', true, NOW(), NOW()) "
                        "RETURNING id"
                    ),
                    {
                        "tid": tenant_id,
                        "sid": style_id,
                        "bid": blogger_id,
                        "code": code,
                        "scode": f"ST{suffix}A",
                    },
                )
            ).scalar_one()
            promo_ids[code] = pid
        # promo2 已有 settlement（模拟事件创建）→ 触发 UNIQUE(promotion_id) 冲突
        await s.execute(
            text(
                "INSERT INTO settlement (id, tenant_id, promotion_id, blogger_id, "
                "style_id, settlement_no, amount, total_amount, settlement_status, "
                "request_event_id, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :tid, :pid, :bid, :sid, :sno, 500.00, "
                "500.00, '待核查', gen_random_uuid(), NOW(), NOW())"
            ),
            {
                "tid": tenant_id,
                "pid": promo_ids[promo2_code],
                "bid": blogger_id,
                "sid": style_id,
                "sno": f"EX{suffix}",
            },
        )
        await s.execute(
            text(
                "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                "original_filename, file_r2_key, file_bucket, status, total_rows, "
                "imported, failed, retry_count, created_at, updated_at) "
                "VALUES (:id, :tid, 'manual_settlement', :h, 'settles.csv', :k, "
                "'private', 'processing', 0, 0, 0, 0, NOW(), NOW())"
            ),
            {
                "id": batch_id,
                "tid": tenant_id,
                "h": suffix,
                "k": f"imports/{tenant_id}/{batch_id}/settles.csv",
            },
        )
        await s.commit()
    return tenant_id, promo1_code, promo2_code


async def _cleanup(Maker, suffix: str, batch_id):
    async with Maker() as c:
        await c.execute(
            text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id}
        )
        await c.execute(
            text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id}
        )
        # settlement 引用 promotion/style/blogger，先删 settlement
        await c.execute(
            text(
                "DELETE FROM settlement WHERE promotion_id IN "
                "(SELECT id FROM promotion WHERE style_code_snapshot = :code)"
            ),
            {"code": f"ST{suffix}A"},
        )
        await c.execute(
            text(
                "DELETE FROM settlement_sequence WHERE tenant_id IN "
                "(SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1) "
                "AND date_key = '2026-06-01'"
            )
        )
        await c.execute(
            text("DELETE FROM promotion WHERE style_code_snapshot = :code"),
            {"code": f"ST{suffix}A"},
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
class TestSettlementImportEndToEnd:
    async def test_end_to_end_derive_sequence_and_conflicts(
        self, engine: Any, monkeypatch, event_capture: list
    ) -> None:
        """1 成功（promotion 派生 + settlement_no）+ 1 重复 promotion（UNIQUE 冲突）
        + 1 缺 promotion → partial；且不触发事件。"""
        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(SettlementImportAdapter())

        suffix = uuid4().hex[:8]
        batch_id = uuid4()
        tenant_id, promo1_code, promo2_code = await _seed(Maker, suffix, batch_id)

        csv_bytes = (
            "推广编号,结算日期,金额,总金额,付款金额,结算状态\n"
            f"{promo1_code},2026-06-01,500.00,1500.00,1500.00,待付款\n"
            f"{promo2_code},2026-06-01,500.00,500.00,,待核查\n"
            f"PR{suffix}MISSING,2026-06-01,100,100,,待核查\n"
        ).encode("utf-8")

        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "partial"
            assert result["imported"] == 1
            assert result["failed"] == 2

            async with Maker() as check:
                # 新 settlement（promo1）：派生 blogger/style + settlement_no + 合成 event_id
                row = (
                    await check.execute(
                        text(
                            "SELECT s.settlement_no, s.blogger_id, s.style_id, "
                            "s.settlement_status, s.request_event_id, s.tenant_id, "
                            "s.total_amount, p.blogger_id, p.style_id "
                            "FROM settlement s JOIN promotion p ON p.id = s.promotion_id "
                            "WHERE p.internal_code = :code"
                        ),
                        {"code": promo1_code},
                    )
                ).fetchone()
                assert row is not None
                # settlement_no 格式：<2位前缀>S<yymmdd><0001>
                assert "S260601" in row[0]
                # 派生：settlement.blogger_id/style_id == promotion 的
                assert str(row[1]) == str(row[7])
                assert str(row[2]) == str(row[8])
                assert row[3] == "待付款"
                assert row[4] is not None  # 合成 request_event_id 非空
                assert str(row[5]) == str(tenant_id)  # 跨租户正确
                assert str(row[6]) == "1500.00"

                jobs = (
                    await check.execute(
                        text(
                            "SELECT row_number, status, error_detail FROM import_job "
                            "WHERE batch_id = :b ORDER BY row_number"
                        ),
                        {"b": batch_id},
                    )
                ).fetchall()
                assert [j[1] for j in jobs] == ["success", "failed", "failed"]
                # row2：重复 promotion → UNIQUE 冲突 FB3
                assert "已有结算单" in (jobs[1][2] or "")
                # row3：promotion 不存在
                assert "推广编号" in (jobs[2][2] or "") and "不存在" in (
                    jobs[2][2] or ""
                )

            # 不触发事件（导入是数据迁移，区别 U05 service）
            assert event_capture == []
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)


    async def test_full_row_all_fields_persisted(
        self, engine: Any, monkeypatch, event_capture: list
    ) -> None:
        """单行全字段（付款金额/付款日期/笔记标题/备注）→ success 且全部入库。"""
        Maker = async_sessionmaker(
            engine, expire_on_commit=False, class_=AsyncSession
        )
        monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
        monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
        ImportAdapterRegistry.clear()
        ImportAdapterRegistry.register(SettlementImportAdapter())

        suffix = uuid4().hex[:8]
        batch_id = uuid4()
        tenant_id, promo1_code, _ = await _seed(Maker, suffix, batch_id)

        # "1,299.00" 含千分位逗号，须用引号包裹（验证 _to_decimal 去千分位）
        csv_bytes = (
            "推广编号,结算日期,金额,总金额,付款金额,付款日期,结算状态,笔记标题,备注\n"
            f'{promo1_code},2026-06-01,"1,299.00","1,299.00",1299.00,2026-06-10,'
            "已付款,夏季新款,首笔结算\n"
        ).encode("utf-8")

        import app.core.attachment as att_mod

        monkeypatch.setattr(
            att_mod.attachment_service,
            "get_object_bytes",
            lambda bucket, key: csv_bytes,
        )

        try:
            result = await _run_import_batch(batch_id, only_failed=False)
            assert result["status"] == "completed"
            assert result["imported"] == 1

            async with Maker() as check:
                row = (
                    await check.execute(
                        text(
                            "SELECT s.amount, s.total_amount, s.payment_amount, "
                            "s.payment_date, s.settlement_status, s.note_title, s.remark "
                            "FROM settlement s JOIN promotion p ON p.id = s.promotion_id "
                            "WHERE p.internal_code = :code"
                        ),
                        {"code": promo1_code},
                    )
                ).fetchone()
                assert str(row[0]) == "1299.00"
                assert str(row[1]) == "1299.00"
                assert str(row[2]) == "1299.00"
                assert str(row[3]) == "2026-06-10"
                assert row[4] == "已付款"
                assert row[5] == "夏季新款"
                assert row[6] == "首笔结算"
            assert event_capture == []
        finally:
            ImportAdapterRegistry.clear()
            await _cleanup(Maker, suffix, batch_id)
