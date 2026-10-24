"""主线事件的类型表与仓储形状（流程线设计 6.3、6.4；9.3 G 块「只追加、同事务」的结构部分）。

落库行为（同事务回滚、按 seq 排、8 KB 上限、建单写 promotion.created）在 ``tests/integration/test_line_events.py``。
"""

from __future__ import annotations

import inspect

import pytest

from app.modules.flow.exceptions import LineEventPayloadTooLargeError
from app.modules.flow.line_events import (
    _GROUP_MEMBERS,
    EVENT_GROUP,
    EVENT_GROUP_KEYS,
    PAYLOAD_MAX_BYTES,
    LineEventRepository,
    LineEventType,
    payload_size,
)
from app.modules.flow.models import LineEvent


def test_repository_public_methods_are_append_and_list_only() -> None:
    """只追加（6.4）：除 append / list_by_line 外没有公开方法，更没有 update / delete。"""
    public = {
        name
        for name, _ in inspect.getmembers(LineEventRepository, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    assert public == {"append", "list_by_line"}
    other_attrs = {n for n in vars(LineEventRepository) if not n.startswith("_")} - public
    assert other_attrs == set()


def test_append_signature_requires_tenant_and_actor_keywords() -> None:
    """``tenant_id`` 显式传（不依赖上下文）、``actor_id`` 必须表态（系统写的事件传 None）。"""
    params = inspect.signature(LineEventRepository.append).parameters
    for name in ("tenant_id", "line_id", "event_type", "actor_id"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert params[name].default is inspect.Parameter.empty, name


def test_event_group_covers_every_type_exactly_once() -> None:
    registered = [t for members in _GROUP_MEMBERS.values() for t in members]
    assert len(registered) == len(set(registered)), "有事件类型登记了两个分组"
    assert set(EVENT_GROUP) == set(LineEventType)
    assert set(_GROUP_MEMBERS) == set(EVENT_GROUP_KEYS)
    for key in EVENT_GROUP_KEYS:
        assert any(g == key for g in EVENT_GROUP.values()), f"分组 {key} 没有事件类型"


def test_event_type_names_fit_column_and_follow_naming() -> None:
    """``<对象>.<动作>``，且放得进 ``event_type varchar(40)``。"""
    width = LineEvent.__table__.c.event_type.type.length
    for t in LineEventType:
        obj, _, action = t.value.partition(".")
        assert obj and action and "." not in action, t.value
        assert len(t.value) <= width, t.value


@pytest.mark.parametrize(
    ("event_type", "group"),
    [
        (LineEventType.NEGOTIATION_SUBMITTED, "negotiation"),
        (LineEventType.LINE_COMMENT, "comment"),
        (LineEventType.PROMOTION_CREATED, "promotion"),
        (LineEventType.URGE_TASK_OPENED, "promotion"),
        (LineEventType.RECALL_FAILED, "promotion"),
        (LineEventType.SHIP_PUSHED, "shipping"),
        (LineEventType.PROMOTION_PAYMENT_QR_UPLOADED, "settlement"),
        (LineEventType.FREIGHT_PAID, "settlement"),
        (LineEventType.PROMOTION_SETTLED, "settlement"),
        (LineEventType.RETRO_EDITED, "retro"),
        (LineEventType.RETRO_CONFIRMED, "retro"),
        (LineEventType.RETRO_RETURNED, "retro"),
    ],
)
def test_event_group_follows_design_table(event_type: LineEventType, group: str) -> None:
    """6.3 表的分组列：对象前缀与分组不一一对应的几类（催发 / 召回归推广，收款码 / 运费归结款）。"""
    assert EVENT_GROUP[event_type] == group


def test_payload_size_uses_jsonb_text_bytes() -> None:
    """与 ``octet_length(payload::text)`` 同口径：UTF-8 字节、``", "`` / ``": "`` 分隔。"""
    assert payload_size({}) == len("{}")
    assert payload_size({"a": 1, "b": [1, 2]}) == len('{"a": 1, "b": [1, 2]}')
    assert payload_size({"来源": "直接新建"}) == len('{"来源": "直接新建"}'.encode())


def test_payload_too_large_error_shape() -> None:
    exc = LineEventPayloadTooLargeError(
        event_type="promotion.created", size=PAYLOAD_MAX_BYTES + 1, limit=PAYLOAD_MAX_BYTES
    )
    assert (exc.code, exc.status_code) == ("LINE_EVENT_PAYLOAD_TOO_LARGE", 422)
    assert exc.details == {
        "event_type": "promotion.created",
        "size": PAYLOAD_MAX_BYTES + 1,
        "limit": PAYLOAD_MAX_BYTES,
    }


@pytest.mark.asyncio
async def test_event_query_returns_full_history_completion_flag_in_one_statement() -> None:
    """完整历史标志随原事件查询返回，不能由header另查line_event。真实截断由PG集成验。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from sqlalchemy.dialects import postgresql

    item = LineEvent(event_type="line.comment")
    result = Mock()
    result.all.return_value = [(item, True)]
    result.scalars.return_value.all.return_value = [item]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    rows = await LineEventRepository(session).list_by_line(
        tenant_id=uuid4(), line_id=uuid4(), newest_first=True, limit=501
    )
    assert list(rows) == [item]
    assert rows.has_completion_event
    session.execute.assert_awaited_once()
    sql = str(session.execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert sql.count("FROM line_event") == 1
    assert "bool_or(" in sql and "OVER ()" in sql and "LIMIT" in sql
