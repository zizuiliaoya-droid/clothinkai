"""流程线 PR-6：两个跨模块强一致事件的注册（设计 7.5、9.2「事件监听注册」）。

CI 的 validate-event-listeners 只 grep finance 的导入，所以这里直接调应用启动的注册入口，
确认 ``SettlementRejected`` / ``SettlementResubmitted`` 都有监听，且缺监听时 dispatch 报错。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.core.events import clear_handlers, dispatch, get_handlers
from app.core.exceptions import MissingRequiredHandlerError
from app.modules.finance.events import SettlementRejected
from app.modules.finance.listeners import on_settlement_resubmitted
from app.modules.promotion.events import SettlementResubmitted
from app.modules.promotion.listeners import on_settlement_rejected


def _rejected() -> SettlementRejected:
    return SettlementRejected(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        tenant_id=uuid4(),
        settlement_id=uuid4(),
        promotion_id=uuid4(),
        reason="收款码不对",
        actor_id=uuid4(),
        from_state="待付款",
    )


def _resubmitted() -> SettlementResubmitted:
    return SettlementResubmitted(
        event_id=uuid4(),
        timestamp=datetime.now(UTC),
        tenant_id=uuid4(),
        promotion_id=uuid4(),
        settlement_id=uuid4(),
        note="换好收款码了",
        actor_id=uuid4(),
    )


def test_app_startup_registers_both_listeners() -> None:
    from app.main import register_event_listeners

    try:
        register_event_listeners()
        assert get_handlers("SettlementRejected") == [on_settlement_rejected]
        assert get_handlers("SettlementResubmitted") == [on_settlement_resubmitted]
    finally:
        clear_handlers()


def test_both_events_require_handler() -> None:
    assert SettlementRejected.required_handler is True
    assert SettlementResubmitted.required_handler is True


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_rejected, _resubmitted])
async def test_dispatch_without_listener_raises(make: Callable[[], Any]) -> None:
    clear_handlers()
    event = make()
    with pytest.raises(MissingRequiredHandlerError) as exc_info:
        await dispatch(event, session=None)
    assert exc_info.value.details == {"event_type": event.event_type}
