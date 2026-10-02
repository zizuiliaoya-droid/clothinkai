"""定时刷新任务的逐租户隔离与「忙」的处理（不连库，用替身）。

不用真库是刻意的：``_refresh_all`` 会刷**库里所有**活跃租户并真实提交，测试库里有
003 seed 的默认租户，跑一次就会在它名下留下汇总行与覆盖记录。这里要验证的只是
任务的分支逻辑，替身足够。

两件事：

1. **「忙」不是故障**：有人在手动刷新时定时任务撞上锁，应该跳过这一轮、不报 Sentry。
   当成故障上报的话，每次手动刷新都会刷出一条告警，真故障被淹没。
2. **单租户失败不中断其他租户**：一个租户的数据有问题不该让其他租户的报表也停在
   旧数字上。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest

import app.tasks.summary_tasks as tasks
from app.modules.report.exceptions import SummaryRefreshBusyError

_COUNTS = {
    "product_roi_summary": 3,
    "pr_work_progress_summary": 2,
    "shop_daily_summary": 31,
    "shop_week_summary": 5,
    "shop_month_summary": 2,
}


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, tenant_ids: list[UUID]) -> None:
        self._tenant_ids = tenant_ids
        self.commits = 0

    async def execute(self, *_a: Any, **_k: Any) -> _FakeResult:
        return _FakeResult(self._tenant_ids)

    async def commit(self) -> None:
        self.commits += 1


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    tenant_ids: list[UUID],
    behaviour: dict[UUID, Exception | None],
) -> dict[str, Any]:
    """装替身：租户列表、session、刷新服务、Sentry。返回记录器。"""
    rec: dict[str, Any] = {"sentry": [], "refreshed": [], "commits": 0}

    @asynccontextmanager
    async def _session() -> AsyncIterator[_FakeSession]:
        s = _FakeSession(tenant_ids)
        yield s
        rec["commits"] += s.commits

    class _FakeService:
        def __init__(self, _s: Any) -> None:
            pass

        async def refresh(self, *, tenant_id: UUID, **_k: Any) -> dict[str, int]:
            exc = behaviour.get(tenant_id)
            if exc is not None:
                raise exc
            rec["refreshed"].append(tenant_id)
            return dict(_COUNTS)

    monkeypatch.setattr(tasks, "AsyncSessionBypass", _session)
    monkeypatch.setattr(tasks, "AsyncSessionApp", _session)
    monkeypatch.setattr(tasks, "SummaryRefreshService", _FakeService)
    monkeypatch.setattr(tasks.sentry_sdk, "capture_exception", rec["sentry"].append)
    return rec


@pytest.mark.asyncio
class TestBusyIsNotAFailure:
    async def test_busy_tenant_is_skipped_without_sentry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        busy_t, ok_t = uuid4(), uuid4()
        rec = _patch(monkeypatch, [busy_t, ok_t], {busy_t: SummaryRefreshBusyError()})

        out = await tasks._refresh_all(31)

        assert out["busy"] == 1
        assert out["failed"] == 0
        assert rec["sentry"] == [], "撞锁被当成故障上报了 —— 每次手动刷新都会产生一条告警"
        # 忙的租户被跳过，另一个照常刷新并提交
        assert rec["refreshed"] == [ok_t]
        assert out["product_roi_summary"] == _COUNTS["product_roi_summary"]


@pytest.mark.asyncio
class TestTenantIsolation:
    async def test_one_tenant_failure_does_not_stop_others(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bad_t, ok_1, ok_2 = uuid4(), uuid4(), uuid4()
        boom = RuntimeError("脏数据")
        rec = _patch(monkeypatch, [ok_1, bad_t, ok_2], {bad_t: boom})

        out = await tasks._refresh_all(31)

        assert out["tenants"] == 3
        assert out["failed"] == 1
        assert out["busy"] == 0
        assert rec["refreshed"] == [ok_1, ok_2], "失败的租户中断了后面租户的刷新"
        # 真故障要上报，而且只报那一个
        assert rec["sentry"] == [boom]
        # 两个成功租户的行数累加
        assert out["shop_daily_summary"] == 2 * _COUNTS["shop_daily_summary"]

    async def test_failed_tenant_is_not_committed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """刷新抛错的租户不能 commit —— 那会把删了一半的汇总表提交出去。"""
        bad_t = uuid4()
        rec = _patch(monkeypatch, [bad_t], {bad_t: RuntimeError("x")})

        await tasks._refresh_all(31)

        assert rec["commits"] == 0
