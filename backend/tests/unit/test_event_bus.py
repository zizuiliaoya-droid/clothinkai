"""U04 core/events.py 单元测试（FB1 / FB4 / FB6 守护）.

覆盖：
- subscribe 幂等（FB6）
- clear_handlers 后重新 subscribe 正常
- required_handler=True 无 handler → MissingRequiredHandlerError（FB1/FB4）
- required_handler=False 无 handler → no-op（FB4）
- handler 抛异常自然冒泡（FB1：触发事务回滚）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import pytest

from app.core.events import (
    clear_handlers,
    dispatch,
    get_handlers,
    subscribe,
    unsubscribe,
)
from app.core.exceptions import MissingRequiredHandlerError


@dataclass(frozen=True)
class _RequiredEvent:
    event_type: ClassVar[str] = "test.required"
    required_handler: ClassVar[bool] = True
    payload: str = "x"


@dataclass(frozen=True)
class _OptionalEvent:
    event_type: ClassVar[str] = "test.optional"
    required_handler: ClassVar[bool] = False
    payload: str = "x"


@pytest.fixture(autouse=True)
def _clear_before_each() -> Any:
    clear_handlers()
    yield
    clear_handlers()


class TestSubscribeIdempotent:
    """FB6: 同一 (event_type, handler) 重复注册仅生效一次."""

    @pytest.mark.asyncio
    async def test_subscribe_idempotent_dispatch_once(self) -> None:
        calls: list[Any] = []

        async def handler(event: Any, _session: Any) -> None:
            calls.append(event)

        subscribe("test.optional", handler)
        subscribe("test.optional", handler)  # 重复
        subscribe("test.optional", handler)  # 重复
        assert len(get_handlers("test.optional")) == 1

        await dispatch(_OptionalEvent(), session=None)
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_clear_handlers_then_resubscribe(self) -> None:
        calls: list[Any] = []

        async def h1(event: Any, _s: Any) -> None:
            calls.append(("h1", event))

        async def h2(event: Any, _s: Any) -> None:
            calls.append(("h2", event))

        subscribe("test.optional", h1)
        clear_handlers()
        subscribe("test.optional", h2)
        await dispatch(_OptionalEvent(), session=None)
        assert calls == [("h2", _OptionalEvent(payload="x"))]

    def test_unsubscribe(self) -> None:
        async def handler(_e: Any, _s: Any) -> None:
            pass

        subscribe("test.optional", handler)
        assert len(get_handlers("test.optional")) == 1
        unsubscribe("test.optional", handler)
        assert len(get_handlers("test.optional")) == 0


class TestRequiredVsOptional:
    @pytest.mark.asyncio
    async def test_required_event_no_handler_raises(self) -> None:
        """FB1: 强一致事件无 handler → 抛 MissingRequiredHandlerError."""
        with pytest.raises(MissingRequiredHandlerError) as exc:
            await dispatch(_RequiredEvent(), session=None)
        assert exc.value.code == "MISSING_REQUIRED_HANDLER"
        assert exc.value.details["event_type"] == "test.required"

    @pytest.mark.asyncio
    async def test_optional_event_no_handler_noop(self) -> None:
        """FB4: 通知类事件无 handler → no-op，不抛错."""
        # 不抛错即通过
        await dispatch(_OptionalEvent(), session=None)


class TestHandlerFailure:
    """FB1: handler 抛异常自然冒泡，调用方触发事务回滚."""

    @pytest.mark.asyncio
    async def test_handler_exception_propagates(self) -> None:
        async def failing_handler(_e: Any, _s: Any) -> None:
            raise RuntimeError("simulated downstream failure")

        subscribe("test.required", failing_handler)
        with pytest.raises(RuntimeError, match="simulated downstream failure"):
            await dispatch(_RequiredEvent(), session=None)

    @pytest.mark.asyncio
    async def test_multiple_handlers_first_failure_aborts(self) -> None:
        """多 handler 时第一个抛异常即冒泡，后续 handler 不再执行."""
        executed: list[str] = []

        async def h1(_e: Any, _s: Any) -> None:
            executed.append("h1")
            raise RuntimeError("h1 failed")

        async def h2(_e: Any, _s: Any) -> None:
            executed.append("h2")

        subscribe("test.required", h1)
        subscribe("test.required", h2)
        with pytest.raises(RuntimeError, match="h1 failed"):
            await dispatch(_RequiredEvent(), session=None)
        assert executed == ["h1"]


class TestEventTypeValidation:
    @pytest.mark.asyncio
    async def test_event_without_event_type_raises(self) -> None:
        @dataclass(frozen=True)
        class _Bad:
            payload: str = "x"

        with pytest.raises(ValueError, match="missing event_type"):
            await dispatch(_Bad(), session=None)
