"""导入完成后自动刷新报表汇总表（方案 2）的规则（不连库）。

三件事：

1. **刷哪些日子**（``target_runs``）：只刷已被覆盖的、和在每小时窗口里的。没覆盖的历史
   不刷 —— 它走实时，数字永远是新的；刷一次反而让它从此冻结。
2. **记哪些日子**（``_AffectedDates``）：只记成功提交的行、只认 adapter 声明的业务日期列。
3. **哪些来源会触发**：写进汇总表依赖的那四张表（千牛日报 / 万相台日报 / 推广单 / 刷单）
   的 adapter 必须声明业务日期列。以后新增一个写这几张表的 adapter 却忘了声明，
   导入后报表就静默不刷新 —— 这里会红。

另有撞锁重试：导入触发的刷新不能像每小时那次一样「撞锁就跳过」，窗口外的日子没有
下一个小时。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

import app.tasks.summary_tasks as summary_tasks
from app.modules.report.exceptions import SummaryRefreshBusyError
from app.tasks.import_tasks import _AffectedDates
from app.tasks.summary_tasks import target_runs

TODAY = date(2026, 10, 2)
WINDOW_LO = TODAY - timedelta(days=30)


def _days(lo: date, hi: date) -> list[date]:
    return [lo + timedelta(days=i) for i in range((hi - lo).days + 1)]


class TestTargetRuns:
    def test_window_days_are_refreshed_even_if_not_yet_covered(self) -> None:
        """窗口内的日子一律刷 —— 刚上线覆盖记录还是空的时候也一样。"""
        lo, hi = TODAY - timedelta(days=5), TODAY
        assert target_runs(lo, hi, [], WINDOW_LO, TODAY) == [(lo, hi)]

    def test_uncovered_history_is_left_alone(self) -> None:
        """三个月前、从没刷过的日子：不刷，让它继续走实时。"""
        lo, hi = date(2026, 6, 1), date(2026, 6, 30)
        assert target_runs(lo, hi, [], WINDOW_LO, TODAY) == []

    def test_covered_history_is_refreshed(self) -> None:
        """已经读汇总表的历史日子，不刷就是旧数字。"""
        lo, hi = date(2026, 6, 1), date(2026, 6, 30)
        assert target_runs(lo, hi, _days(lo, hi), WINDOW_LO, TODAY) == [(lo, hi)]

    def test_gaps_split_into_contiguous_runs(self) -> None:
        covered = _days(date(2026, 6, 1), date(2026, 6, 5)) + _days(
            date(2026, 6, 10), date(2026, 6, 12)
        )
        assert target_runs(date(2026, 6, 1), date(2026, 6, 15), covered, WINDOW_LO, TODAY) == [
            (date(2026, 6, 1), date(2026, 6, 5)),
            (date(2026, 6, 10), date(2026, 6, 12)),
        ]

    def test_history_and_window_merge_when_adjacent(self) -> None:
        """覆盖的历史紧挨着窗口时，合成一段刷，不拆。"""
        lo = WINDOW_LO - timedelta(days=3)
        covered = _days(lo, WINDOW_LO - timedelta(days=1))
        assert target_runs(lo, TODAY, covered, WINDOW_LO, TODAY) == [(lo, TODAY)]

    def test_future_dates_are_skipped(self) -> None:
        """导入了排期在未来的推广单：未来的日子刷不「完整」，不刷。"""
        lo, hi = TODAY - timedelta(days=1), TODAY + timedelta(days=10)
        assert target_runs(lo, hi, [], WINDOW_LO, TODAY) == [(lo, TODAY)]

    def test_single_day(self) -> None:
        assert target_runs(TODAY, TODAY, [], WINDOW_LO, TODAY) == [(TODAY, TODAY)]


class TestAffectedDates:
    def test_tracks_min_and_max(self) -> None:
        a = _AffectedDates("date")
        for d in (date(2026, 3, 9), date(2026, 3, 2), date(2026, 3, 20)):
            a.add({"date": d})
        assert (a.lo, a.hi) == (date(2026, 3, 2), date(2026, 3, 20))

    def test_ignores_rows_without_a_real_date(self) -> None:
        """日期没解析出来（字符串 / 空）的行不进范围 —— 那种行 validate 本来也过不了。"""
        a = _AffectedDates("date")
        a.add({"date": "2026-13-45"})
        a.add({"date": None})
        a.add({})
        assert a.lo is None and a.hi is None

    def test_datetime_is_normalized_to_date(self) -> None:
        a = _AffectedDates("date")
        a.add({"date": datetime(2026, 3, 5, 23, 59)})
        assert a.lo == date(2026, 3, 5)
        assert type(a.lo) is date

    def test_no_field_means_no_trigger(self) -> None:
        """不声明业务日期列的来源（博主、款式……）永远不触发刷新。"""
        a = _AffectedDates(None)
        a.add({"date": date(2026, 3, 5)})
        assert a.lo is None


class TestAdaptersDeclareBusinessDate:
    # 汇总表从这四张表聚合；写它们的 adapter 必须声明业务日期列
    SUMMARY_SOURCE_TABLES = frozenset(
        {"qianniu_daily", "ad_daily", "promotion", "order_adjustment"}
    )

    @pytest.fixture
    def adapters(self) -> Any:
        from app.main import register_import_adapters
        from app.modules.importer.registry import ImportAdapterRegistry

        # 注册表是进程级全局状态，runner 的集成测试会 clear() 再塞 FakeAdapter。
        # 这里用完原样放回，不让测试顺序影响彼此。
        saved = dict(ImportAdapterRegistry._adapters)
        register_import_adapters()
        try:
            yield [ImportAdapterRegistry.get(s) for s in ImportAdapterRegistry.sources()]
        finally:
            ImportAdapterRegistry._adapters.clear()
            ImportAdapterRegistry._adapters.update(saved)

    def test_every_summary_source_adapter_declares_a_date(self, adapters: list[Any]) -> None:
        relevant = [a for a in adapters if a.target_table in self.SUMMARY_SOURCE_TABLES]
        # 当前是 5 个 source：qianniu / wanxiangtai / manual_promotion / 拍单 / 刷单
        assert len(relevant) >= 5, "注册的 adapter 少了，测试前提不成立"
        for a in relevant:
            assert getattr(a, "summary_date_field", None), (
                f"{a.source} 写 {a.target_table} 却没声明 summary_date_field，"
                "导入后报表汇总表不会刷新"
            )

    def test_declared_field_is_a_real_parsed_field(self, adapters: list[Any]) -> None:
        """声明的列名必须是 parse_row 真的会产出的字段 —— 拼错了等于没声明。"""
        import importlib

        for a in adapters:
            field = getattr(a, "summary_date_field", None)
            if not field:
                continue
            module = importlib.import_module(type(a).__module__)
            targets = {c["target_field"] for c in module._DEFAULT_COLUMNS}
            assert field in targets, f"{a.source}.summary_date_field={field!r} 不在默认列映射里"

    def test_other_adapters_do_not_trigger(self, adapters: list[Any]) -> None:
        for a in adapters:
            if a.target_table not in self.SUMMARY_SOURCE_TABLES:
                assert not getattr(
                    a, "summary_date_field", None
                ), f"{a.source} 写的 {a.target_table} 不进汇总表，不该触发刷新"


class TestBusyRetry:
    """导入触发的刷新撞锁要重试，不能丢。

    用 ``task.apply()`` 在进程内同步执行：Celery 的 eager 模式会把 ``self.retry`` 直接
    原地重放，所以能数出到底跑了几次。
    """

    def test_retries_until_lock_is_free(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        async def fake(*_a: Any) -> dict[str, Any]:
            calls.append(1)
            if len(calls) < 3:
                raise SummaryRefreshBusyError()
            return {"ok": True}

        monkeypatch.setattr(summary_tasks, "_refresh_dates", fake)
        res = summary_tasks.refresh_report_summaries_for_dates.apply(
            args=["00000000-0000-0000-0000-000000000001", "2026-03-01", "2026-03-05"]
        )
        assert res.state == "SUCCESS"
        assert res.result == {"ok": True}
        assert len(calls) == 3

    def test_gives_up_after_max_retries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []

        async def always_busy(*_a: Any) -> dict[str, Any]:
            calls.append(1)
            raise SummaryRefreshBusyError()

        monkeypatch.setattr(summary_tasks, "_refresh_dates", always_busy)
        res = summary_tasks.refresh_report_summaries_for_dates.apply(
            args=["00000000-0000-0000-0000-000000000001", "2026-03-01", "2026-03-05"]
        )
        assert res.state == "FAILURE"
        assert len(calls) == summary_tasks._BUSY_MAX_RETRIES + 1

    def test_task_runs_on_report_queue(self) -> None:
        assert summary_tasks.refresh_report_summaries_for_dates.queue == "report"
