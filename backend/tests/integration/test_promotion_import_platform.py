"""8b：推广单导入按（推广平台, 账号）找博主，找不到时按账号跨平台回落（设计 §3.9、§9、§10）。

走 runner：推广单 CSV 中文表头（千分位、多余列）→ adapter → promotion 入库。推广单的「平台」是发布平台，只当首选：
① 同平台有 → 用它；② 同平台没有、账号只在 1 个平台有 → 用它，``promotion.platform`` 仍按文件；
③ 账号在 ≥ 2 个平台都有 → 行失败，提示填博主所在平台（平台名按枚举序）；④ 哪儿都没有 → 「博主 X 不存在」。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as tasks
from app.modules.importer.adapters.promotion import PromotionImportAdapter
from app.modules.importer.registry import ImportAdapterRegistry
from app.tasks.import_tasks import _run_import_batch

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_HEADER = "款式编码,小红书ID,报价金额,平台,合作日期,多余列\n"
_DATE = "2026-06-02"


@dataclass(frozen=True)
class _Outcome:
    account: str
    blogger_ids: dict[str, UUID]  # 平台 → 博主 id（同一账号）
    jobs: list[Any]  # (row_number, status, error_detail)
    promos: list[Any]  # (blogger_id, platform, quote_amount)


async def _seed(Maker: Any, suffix: str, batch_id: UUID, account: str, platforms: list[str]):
    """committed seed：style ST<suffix> + 同一账号在给定平台上各一个博主 + processing 批次。"""
    ids: dict[str, UUID] = {}
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
            {"tid": tenant_id, "code": f"ST{suffix}"},
        )
        for platform in platforms:
            bid = uuid4()
            await s.execute(
                text(
                    "INSERT INTO blogger (id, tenant_id, xiaohongshu_id, nickname, platform, "
                    "is_suspected_fake, is_active, is_deleted, created_at, updated_at) "
                    "VALUES (:id, :tid, :acc, '测试博主', :pf, false, true, false, NOW(), NOW())"
                ),
                {"id": bid, "tid": tenant_id, "acc": account, "pf": platform},
            )
            ids[platform] = bid
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
    return ids


async def _cleanup(Maker: Any, suffix: str, batch_id: UUID, account: str) -> None:
    async with Maker() as c:
        await c.execute(text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id})
        await c.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
        await c.execute(
            text("DELETE FROM promotion WHERE style_code_snapshot = :code"),
            {"code": f"ST{suffix}"},
        )
        await c.execute(
            text(
                "DELETE FROM promotion_sequence WHERE tenant_id IN "
                "(SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1) "
                "AND date_key = CAST(:d AS date)"
            ),
            {"d": date.fromisoformat(_DATE)},
        )
        await c.execute(text("DELETE FROM blogger WHERE xiaohongshu_id = :a"), {"a": account})
        await c.execute(text("DELETE FROM style WHERE style_code = :code"), {"code": f"ST{suffix}"})
        await c.commit()


async def _run(engine: Any, monkeypatch: Any, *, platforms: list[str], rows: list[str]) -> _Outcome:
    """同一账号在 ``platforms`` 上各建一个博主；按 ``rows`` 里的推广平台各导一行。"""
    Maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(tasks, "AsyncSessionApp", Maker)
    monkeypatch.setattr(tasks, "AsyncSessionBypass", Maker)
    ImportAdapterRegistry.clear()
    ImportAdapterRegistry.register(PromotionImportAdapter())

    suffix = uuid4().hex[:10]
    account = f"k{suffix}"
    body = "".join(f'ST{suffix},{account},"1,500.00",{pf},{_DATE},忽略\n' for pf in rows)
    csv_bytes = (_HEADER + body).encode()

    import app.core.attachment as att_mod

    monkeypatch.setattr(att_mod.attachment_service, "get_object_bytes", lambda b, k: csv_bytes)

    batch_id = uuid4()
    ids = await _seed(Maker, suffix, batch_id, account, platforms)
    try:
        await _run_import_batch(batch_id, only_failed=False)
        async with Maker() as s:
            jobs = (
                await s.execute(
                    text(
                        "SELECT row_number, status, error_detail FROM import_job "
                        "WHERE batch_id = :b ORDER BY row_number"
                    ),
                    {"b": batch_id},
                )
            ).fetchall()
            promos = (
                await s.execute(
                    text(
                        "SELECT blogger_id, platform, quote_amount FROM promotion "
                        "WHERE style_code_snapshot = :code ORDER BY internal_code"
                    ),
                    {"code": f"ST{suffix}"},
                )
            ).fetchall()
    finally:
        ImportAdapterRegistry.clear()
        await _cleanup(Maker, suffix, batch_id, account)
    return _Outcome(account, ids, list(jobs), list(promos))


class TestPromotionImportBloggerPlatform:
    async def test_same_platform_hit_prefers_it(self, engine: Any, monkeypatch: Any) -> None:
        """① 推广平台 = 博主平台 → 命中；同账号另一平台也有博主时取同平台那个。"""
        out = await _run(engine, monkeypatch, platforms=["小红书", "抖音"], rows=["抖音", "小红书"])
        assert [j[1] for j in out.jobs] == ["success", "success"]
        ids = out.blogger_ids
        assert ids["小红书"] != ids["抖音"]  # 场景有效性：同账号两个博主
        assert sorted((p[1], p[0]) for p in out.promos) == sorted(
            [("抖音", ids["抖音"]), ("小红书", ids["小红书"])]
        )
        assert {str(p[2]) for p in out.promos} == {"1500.00"}

    async def test_fallback_to_only_platform(self, engine: Any, monkeypatch: Any) -> None:
        """② 推广平台 = 抖音、账号只有小红书博主 → 回落到它，promotion.platform 仍是抖音。"""
        out = await _run(engine, monkeypatch, platforms=["小红书"], rows=["抖音"])
        assert [j[1] for j in out.jobs] == ["success"]
        assert [(p[0], p[1]) for p in out.promos] == [(out.blogger_ids["小红书"], "抖音")]

    async def test_multiple_platforms_without_hit_fails(
        self, engine: Any, monkeypatch: Any
    ) -> None:
        """③ 账号在小红书与抖音都有、推广平台 = 得物 → 行失败，文案含两个平台名（枚举序）。"""
        # 建库顺序故意与枚举序相反，确认文案按枚举序而不是插入序
        out = await _run(engine, monkeypatch, platforms=["抖音", "小红书"], rows=["得物"])
        assert [j[1] for j in out.jobs] == ["failed"]
        assert out.jobs[0][2] == (
            f"RowValidationError: 账号 {out.account} 在多个平台都有博主（小红书、抖音），"
            "请把「平台」列填成博主所在的平台"
        )
        assert out.promos == []

    async def test_no_blogger_anywhere(self, engine: Any, monkeypatch: Any) -> None:
        """④ 账号哪儿都没有 → 照旧「博主 X 不存在」。"""
        out = await _run(engine, monkeypatch, platforms=[], rows=["抖音"])
        assert [j[1] for j in out.jobs] == ["failed"]
        assert out.jobs[0][2] == f"RowValidationError: 博主 {out.account} 不存在"
        assert out.promos == []
