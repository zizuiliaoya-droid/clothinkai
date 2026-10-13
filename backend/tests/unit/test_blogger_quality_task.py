"""7e R7：``recompute_blogger_quality_tags`` 任务（不连库，用替身）。

只验任务自己的分支：任务选项、上下文设置（租户 contextvar + system_context +
``set_config``）、只调 ``recompute_quality_for_tenant``、DB 抖动原样抛给 autoretry、
重试用完那次记 error + Sentry、其他异常不抛（不重试）但记 error + Sentry，返回值带
``ok: False`` 与异常类型名（成功带 ``ok: True``，空租户也是成功）。
重算本身的行为在 ``tests/integration/test_blogger_recompute.py``。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import OperationalError

import app.tasks.blogger_tasks as tasks
from app.core.tenancy import actor_type_ctx, is_bypass_rls, tenant_id_ctx

_TASK = tasks.recompute_blogger_quality_tags
_RESULT = {"updated": 2, "unchanged": 5, "failed": 1}


def _db_error() -> OperationalError:
    return OperationalError("SELECT 1", {}, Exception("connection reset"))


def _fake_task(retries: int) -> Any:
    return SimpleNamespace(request=SimpleNamespace(retries=retries), max_retries=_TASK.max_retries)


def _ctx() -> dict[str, Any]:
    return {"tenant": tenant_id_ctx.get(), "bypass": is_bypass_rls(), "actor": actor_type_ctx.get()}


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    raise_exc: Exception | None = None,
    result: dict[str, int] | None = None,
) -> dict[str, Any]:
    rec: dict[str, Any] = {"sql": [], "calls": [], "commits": 0, "sentry": []}

    class _FakeSession:
        async def execute(self, stmt: Any, params: Any = None) -> None:
            rec["sql"].append((str(stmt), params, _ctx()))

        async def commit(self) -> None:
            rec["commits"] += 1

    @asynccontextmanager
    async def _session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    class _FakeTagService:
        """只有 ``recompute_quality_for_tenant``：任务改调别的重算会直接 AttributeError。"""

        def __init__(self, _s: Any) -> None:
            pass

        async def recompute_quality_for_tenant(self, tenant_id: UUID) -> dict[str, int]:
            rec["calls"].append((tenant_id, _ctx()))
            if raise_exc is not None:
                raise raise_exc
            return dict(_RESULT if result is None else result)

    monkeypatch.setattr(tasks, "AsyncSessionApp", _session)
    monkeypatch.setattr(tasks, "BloggerTagService", _FakeTagService)
    monkeypatch.setattr(tasks.sentry_sdk, "capture_exception", rec["sentry"].append)
    return rec


class TestTaskOptions:
    def test_options(self) -> None:
        assert _TASK.name == "app.tasks.blogger_tasks.recompute_blogger_quality_tags"
        assert _TASK.queue == "default"
        assert tuple(_TASK.autoretry_for) == (OperationalError,)
        assert _TASK.max_retries == 3
        assert _TASK.default_retry_delay == 10

    def test_registered_on_app(self) -> None:
        from app.core.celery_app import celery_app

        assert "app.tasks.blogger_tasks.recompute_blogger_quality_tags" in celery_app.tasks

    def test_run_parses_tenant_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """签名 ``(self, tenant_id: str)``：字符串进、按 UUID 交给执行体，task 自己作为 self。"""
        seen: list[tuple[Any, UUID]] = []

        async def _impl(task: Any, tenant_id: UUID) -> dict[str, int]:
            seen.append((task, tenant_id))
            return dict(_RESULT)

        monkeypatch.setattr(tasks, "_recompute_quality_impl", _impl)
        monkeypatch.setattr(tasks, "run_async_task", asyncio.run)
        tid = uuid4()

        assert _TASK.run(str(tid)) == _RESULT
        assert len(seen) == 1
        assert seen[0][0].name == _TASK.name
        assert seen[0][1] == tid


@pytest.mark.asyncio
class TestTaskBody:
    async def test_context_and_quality_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rec = _patch(monkeypatch)
        tid = uuid4()

        out = await tasks._recompute_quality_impl(_fake_task(0), tid)

        assert out == {**_RESULT, "ok": True}
        assert rec["commits"] == 1
        want_ctx = {"tenant": tid, "bypass": True, "actor": "system"}
        # set_config 在调用重算之前、带同一个租户
        assert len(rec["sql"]) == 1
        sql, params, ctx = rec["sql"][0]
        assert "set_config('app.tenant_id'" in sql
        assert params == {"t": str(tid)}
        assert ctx == want_ctx
        assert rec["calls"] == [(tid, want_ctx)]
        assert rec["sentry"] == []
        # 上下文还原
        assert tenant_id_ctx.get() is None
        assert is_bypass_rls() is False

    async def test_non_db_error_not_raised_but_reported(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        boom = RuntimeError("脏数据")
        rec = _patch(monkeypatch, boom)
        caplog.set_level(logging.ERROR, logger=tasks.__name__)

        out = await tasks._recompute_quality_impl(_fake_task(0), uuid4())

        # 整批失败要和空租户分得开：带失败标记与异常类型名
        assert out == {
            "updated": 0,
            "unchanged": 0,
            "failed": 0,
            "ok": False,
            "error": "RuntimeError",
        }
        assert rec["sentry"] == [boom]
        assert rec["commits"] == 0
        assert any(r.levelno >= logging.ERROR for r in caplog.records)
        assert tenant_id_ctx.get() is None

    async def test_empty_tenant_is_success_with_zero_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        zero = {"updated": 0, "unchanged": 0, "failed": 0}
        rec = _patch(monkeypatch, result=zero)

        out = await tasks._recompute_quality_impl(_fake_task(0), uuid4())

        assert out == {**zero, "ok": True}
        assert rec["commits"] == 1
        assert rec["sentry"] == []

    async def test_db_error_raised_for_retry_without_report(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        exc = _db_error()
        rec = _patch(monkeypatch, exc)
        caplog.set_level(logging.ERROR, logger=tasks.__name__)

        with pytest.raises(OperationalError) as ei:
            await tasks._recompute_quality_impl(_fake_task(1), uuid4())

        assert ei.value is exc
        assert rec["sentry"] == [], "还会重试的那几次不该上报"
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert tenant_id_ctx.get() is None

    async def test_last_retry_db_error_reported_and_raised(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        exc = _db_error()
        rec = _patch(monkeypatch, exc)
        caplog.set_level(logging.ERROR, logger=tasks.__name__)

        with pytest.raises(OperationalError):
            await tasks._recompute_quality_impl(_fake_task(_TASK.max_retries), uuid4())

        assert rec["sentry"] == [exc]
        assert any(r.levelno >= logging.ERROR for r in caplog.records)
