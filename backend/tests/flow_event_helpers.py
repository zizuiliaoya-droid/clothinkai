"""补写事件测试共用的小工具（流程线 PR-4）。

- ``events_of``：读一条线（可按类型）的事件，按 ``seq`` 排
- ``assert_payload_registered``：payload 各层的键都已在 ``PAYLOAD_FIELD_RULES`` 登记——
  没登记的键读接口会删（6.5 白名单），写了等于白写；补写事件的测试都调它
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.flow.line_events import CONTAINER, PAYLOAD_FIELD_RULES, LineEventType
from app.modules.flow.models import LineEvent


async def events_of(
    session: AsyncSession, *, line_id: UUID, event_type: LineEventType | None = None
) -> list[LineEvent]:
    stmt = select(LineEvent).where(LineEvent.line_id == line_id)
    if event_type is not None:
        stmt = stmt.where(LineEvent.event_type == event_type.value)
    return list((await session.execute(stmt.order_by(LineEvent.seq))).scalars().all())


def assert_payload_registered(payload: Mapping[str, Any]) -> None:
    for key, value in payload.items():
        assert key in PAYLOAD_FIELD_RULES, f"payload 键 {key!r} 没在 PAYLOAD_FIELD_RULES 登记"
        if PAYLOAD_FIELD_RULES[key] is CONTAINER:
            assert isinstance(value, Mapping), f"容器键 {key!r} 的值应是 dict"
            assert_payload_registered(value)


__all__ = ["assert_payload_registered", "events_of"]
