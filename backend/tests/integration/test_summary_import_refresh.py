"""导入完成 → 刷新报表汇总表（方案 2）的端到端行为。

两段：

1. **runner → 投递**：真跑一遍 ``_run_import_batch``，确认投递的日期范围只含成功提交的行；
   不声明业务日期的来源不投递；投递失败不影响导入结果。
2. **刷新任务 → 汇总表**：``_refresh_dates`` 对真库执行，确认只刷已覆盖的日子与窗口内的
   日子，没覆盖的历史保持「未覆盖」（继续走实时）。

都用 ``engine`` 真实提交（runner 有自己的 session，跨连接才看得见），结束时清理。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.tasks.import_tasks as import_tasks
import app.tasks.summary_tasks as summary_tasks
from app.modules.importer.registry import ImportAdapterRegistry
from app.modules.promotion.urge_calculator import get_today
from app.tasks.import_tasks import _run_import_batch
from tests.conftest import FakeImportAdapter

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class _DatedFakeAdapter(FakeImportAdapter):
    """FakeImportAdapter + 一列业务日期（模拟千牛日报这类会进报表的来源）。"""

    summary_date_field = "biz_date"

    def parse_row(self, row: dict[str, Any], mapping: Any) -> dict[str, Any]:
        parsed = super().parse_row(row, mapping)
        raw = parsed.get("biz_date")
        parsed["biz_date"] = datetime.strptime(raw, "%Y-%m-%d").date() if raw else None
        return parsed


class _Recorder:
    def __init__(self, *, raise_exc: Exception | None = None) -> None:
        self.calls: list[list[str]] = []
        self.raise_exc = raise_exc

    def __call__(self, args: list[str] | None = None, **_kw: Any) -> None:
        self.calls.append(list(args or []))
        if self.raise_exc is not None:
            raise self.raise_exc


async def _run_batch(
    engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    adapter: Any,
    csv_body: str,
) -> tuple[dict[str, Any], Any]:
    """建一个 processing 批次、跑 runner、清理，返回 (runner 结果, 租户 id)。"""
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr(import_tasks, "AsyncSessionApp", maker)
    monkeypatch.setattr(import_tasks, "AsyncSessionBypass", maker)
    ImportAdapterRegistry.clear()
    ImportAdapterRegistry.register(adapter)

    suffix = uuid4().hex[:8]
    csv_bytes = csv_body.replace("{s}", suffix).encode()

    import app.core.attachment as attachment_mod

    monkeypatch.setattr(
        attachment_mod.attachment_service, "get_object_bytes", lambda bucket, key: csv_bytes
    )

    batch_id = uuid4()
    async with maker() as seed:
        tenant_id = (
            await seed.execute(text("SELECT id FROM tenant ORDER BY created_at ASC LIMIT 1"))
        ).scalar_one()
        await seed.execute(
            text(
                "INSERT INTO import_batch (id, tenant_id, source, file_hash, "
                "original_filename, file_r2_key, file_bucket, status, "
                "total_rows, imported, failed, retry_count, created_at, updated_at) "
                "VALUES (:id, :tid, 'fake_source', :h, 'data.csv', :key, "
                "'private', 'processing', 0, 0, 0, 0, NOW(), NOW())"
            ),
            {"id": batch_id, "tid": tenant_id, "h": suffix, "key": f"imports/{batch_id}.csv"},
        )
        await seed.commit()
    try:
        result = await _run_import_batch(batch_id, only_failed=False)
    finally:
        ImportAdapterRegistry.clear()
        async with maker() as cleanup:
            await cleanup.execute(
                text("DELETE FROM import_job WHERE batch_id = :id"), {"id": batch_id}
            )
            await cleanup.execute(text("DELETE FROM import_batch WHERE id = :id"), {"id": batch_id})
            await cleanup.execute(
                text("DELETE FROM brand WHERE brand_code LIKE :p"), {"p": f"BR{suffix}%"}
            )
            await cleanup.commit()
    return result, tenant_id


class TestRunnerEnqueuesRefresh:
    async def test_enqueues_range_of_committed_rows_only(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """两行成功（3/5、3/20）+ 一行失败（1/1）→ 投递 3/5 ~ 3/20，失败行的日期不算。"""
        rec = _Recorder()
        monkeypatch.setattr(summary_tasks.refresh_report_summaries_for_dates, "apply_async", rec)

        result, tenant_id = await _run_batch(
            engine,
            monkeypatch,
            _DatedFakeAdapter(),
            "brand_code,brand_name,biz_date,_force_fail\n"
            "BR{s}A,品牌A,2026-03-20,0\n"
            "BR{s}B,品牌B,2026-01-01,1\n"
            "BR{s}C,品牌C,2026-03-05,0\n",
        )

        assert result["status"] == "partial"
        assert rec.calls == [[str(tenant_id), "2026-03-05", "2026-03-20"]]

    async def test_source_without_business_date_does_not_enqueue(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """不声明 summary_date_field 的来源（博主、款式……）导入完不刷新报表。"""
        rec = _Recorder()
        monkeypatch.setattr(summary_tasks.refresh_report_summaries_for_dates, "apply_async", rec)

        result, _ = await _run_batch(
            engine,
            monkeypatch,
            FakeImportAdapter(),
            "brand_code,brand_name,_force_fail\nBR{s}A,品牌A,0\n",
        )

        assert result["status"] == "completed"
        assert rec.calls == []

    async def test_all_rows_failed_does_not_enqueue(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rec = _Recorder()
        monkeypatch.setattr(summary_tasks.refresh_report_summaries_for_dates, "apply_async", rec)

        result, _ = await _run_batch(
            engine,
            monkeypatch,
            _DatedFakeAdapter(),
            "brand_code,brand_name,biz_date,_force_fail\nBR{s}A,品牌A,2026-03-20,1\n",
        )

        assert result["status"] == "failed"
        assert rec.calls == []

    async def test_enqueue_failure_does_not_fail_the_import(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis 挂了投递不出去：导入照样算成功，数据已经进库了。"""
        rec = _Recorder(raise_exc=ConnectionError("broker down"))
        monkeypatch.setattr(summary_tasks.refresh_report_summaries_for_dates, "apply_async", rec)
        captured: list[BaseException] = []
        monkeypatch.setattr(import_tasks.sentry_sdk, "capture_exception", captured.append)

        result, _ = await _run_batch(
            engine,
            monkeypatch,
            _DatedFakeAdapter(),
            "brand_code,brand_name,biz_date,_force_fail\nBR{s}A,品牌A,2026-03-20,0\n",
        )

        assert result["status"] == "completed"
        assert len(rec.calls) == 1
        assert len(captured) == 1, "投递失败要上报，不然没人知道导入后没刷新"


class TestRefreshDates:
    async def _tenant(self, maker: Any) -> Any:
        tid = uuid4()
        async with maker() as s:
            await s.execute(
                text(
                    "INSERT INTO tenant (id, code, name, status, created_at, updated_at) "
                    "VALUES (:id, :code, '导入刷新测试', 'active', NOW(), NOW())"
                ),
                {"id": tid, "code": f"imp_sum_{uuid4().hex[:8]}"},
            )
            await s.commit()
        return tid

    async def _cleanup(self, maker: Any, tid: Any) -> None:
        async with maker() as s:
            for table in (
                "report_summary_coverage",
                "product_roi_summary",
                "pr_work_progress_summary",
                "shop_daily_summary",
                "shop_week_summary",
                "shop_month_summary",
            ):
                await s.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tid})
            await s.execute(text("DELETE FROM tenant WHERE id = :t"), {"t": tid})
            await s.commit()

    async def _coverage(self, maker: Any, tid: Any) -> dict[date, datetime]:
        async with maker() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT stat_date, refreshed_at FROM report_summary_coverage "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": tid},
                )
            ).all()
        return {r[0]: r[1] for r in rows}

    async def test_refreshes_covered_history_and_leaves_uncovered_alone(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(summary_tasks, "AsyncSessionApp", maker)
        tid = await self._tenant(maker)
        today = get_today()
        # 窗口之外很远的一段历史：中间 5 天被刷过（覆盖），两边没有
        hist_lo = today - timedelta(days=120)
        covered_lo, covered_hi = hist_lo + timedelta(days=7), hist_lo + timedelta(days=11)
        hist_hi = hist_lo + timedelta(days=20)
        old = datetime(2020, 1, 1, tzinfo=UTC)
        try:
            async with maker() as s:
                await s.execute(
                    text(
                        "INSERT INTO report_summary_coverage "
                        "(id, tenant_id, stat_date, refreshed_at, created_at, updated_at) "
                        "SELECT gen_random_uuid(), :t, d::date, :old, NOW(), NOW() "
                        "FROM generate_series(CAST(:lo AS date), CAST(:hi AS date), "
                        "interval '1 day') d"
                    ),
                    {"t": tid, "lo": covered_lo, "hi": covered_hi, "old": old},
                )
                await s.commit()

            out = await summary_tasks._refresh_dates(tid, hist_lo, hist_hi)

            assert out["runs"] == [[covered_lo.isoformat(), covered_hi.isoformat()]]
            cov = await self._coverage(maker, tid)
            # 覆盖过的那 5 天被重新刷了（时间更新）
            for i in range(5):
                d = covered_lo + timedelta(days=i)
                assert cov[d] > old, f"{d} 没被刷新"
            # 两边没覆盖的历史仍然没覆盖 —— 继续走实时，不被「冻结」
            assert set(cov) == {covered_lo + timedelta(days=i) for i in range(5)}
        finally:
            await self._cleanup(maker, tid)

    async def test_window_days_get_covered(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """导入了最近几天的数据：窗口内的日子立即刷新并记上覆盖。"""
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(summary_tasks, "AsyncSessionApp", maker)
        tid = await self._tenant(maker)
        today = get_today()
        try:
            out = await summary_tasks._refresh_dates(tid, today - timedelta(days=3), today)
            assert out["runs"] == [[(today - timedelta(days=3)).isoformat(), today.isoformat()]]
            cov = await self._coverage(maker, tid)
            assert set(cov) == {today - timedelta(days=i) for i in range(4)}
        finally:
            await self._cleanup(maker, tid)

    async def test_nothing_to_refresh_takes_no_lock(
        self, engine: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """全是没覆盖的历史：一段都不刷，也不该去抢锁（不然会白白挤掉手动刷新）。"""
        maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(summary_tasks, "AsyncSessionApp", maker)
        tid = await self._tenant(maker)
        today = get_today()
        refreshed: list[Any] = []

        async def spy(self: Any, **kw: Any) -> dict[str, int]:
            refreshed.append(kw)
            return {}

        monkeypatch.setattr(summary_tasks.SummaryRefreshService, "refresh", spy)
        try:
            out = await summary_tasks._refresh_dates(
                tid, today - timedelta(days=200), today - timedelta(days=150)
            )
            assert out["runs"] == []
            assert refreshed == []
        finally:
            await self._cleanup(maker, tid)
