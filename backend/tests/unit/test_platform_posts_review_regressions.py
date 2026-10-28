"""PR9 R1：召回中按发布状态投影，最大合法平台输入不被事件大小拒绝。"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.flow.line_events import PAYLOAD_MAX_BYTES, LineEventRepository, payload_size
from app.modules.flow.matrix import ui_for
from app.modules.promotion.schemas import PlatformPostIn, PlatformPostResponse
from app.modules.promotion.service import PromotionService
from tests.unit.fixtures.flow_matrix_expected import DOCS
from tests.unit.test_flow_matrix import PERSONAS


@pytest.mark.parametrize("published", [True, False])
@pytest.mark.parametrize(
    "role", ["pr", "pr_manager", "admin", "operations", "finance", "warehouse"]
)
def test_recalling_platforms_follow_publish_state(published, role):
    doc = replace(
        DOCS["promotion"]["召回中"],
        publish_status="已发布" if published else "已取消",
        published_stage="待主管审核" if published else None,
    )
    fields = ui_for(PERSONAS[role], doc).to_dict(view="detail")["fields"]
    if not published or role in {"finance", "warehouse"}:
        assert "platform_posts" not in fields
    else:
        assert fields["platform_posts"]["state"] == ("read" if role == "operations" else "edit")


@pytest.mark.asyncio
@pytest.mark.parametrize("has_previous", [False, True], ids=["create", "update"])
async def test_maximum_platform_input_preserves_rows_and_bounds_event(monkeypatch, has_previous):
    from app.modules.promotion import platform_posts

    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4())
    promotion = SimpleNamespace(
        id=uuid4(),
        tenant_id=user.tenant_id,
        line_id=uuid4(),
        platform="小红书",
        cooperation_mode="寄拍",
    )
    rows = [
        PlatformPostIn(
            platform=f"{i}" + "平台" * 7 + "甲",
            publish_url="https://example.com/" + "中" * (512 - len("https://example.com/")),
            like_count=2147483647,
            actual_publish_date="2026-10-20",
        )
        for i in range(5)
    ]
    old = (
        [PlatformPostResponse(id=uuid4(), **(row.model_dump() | {"like_count": 1})) for row in rows]
        if has_previous
        else []
    )
    session = AsyncMock()
    svc = PromotionService(session)
    svc._writable_promotion = AsyncMock(return_value=promotion)
    svc._flow_actor = AsyncMock(return_value=PERSONAS["pr"])
    svc._promotion_doc = AsyncMock(return_value=DOCS["promotion"]["待复盘"])
    svc._to_response = AsyncMock(return_value=None)
    svc._audit = SimpleNamespace(log=AsyncMock())
    monkeypatch.setattr(
        platform_posts, "posts_by_promotion", AsyncMock(return_value={promotion.id: old})
    )
    persisted = AsyncMock()
    monkeypatch.setattr(platform_posts, "replace_posts", persisted)
    events = []

    async def append(_self, **kwargs):
        assert payload_size(kwargs["payload"]) <= PAYLOAD_MAX_BYTES
        events.append(kwargs)

    monkeypatch.setattr(LineEventRepository, "append", append)
    await svc.replace_platform_posts(promotion.id, rows, user)
    assert persisted.await_args.kwargs["rows"] == rows
    assert all(len(row.publish_url) == 512 for row in rows)
    assert len(events) == 1
    assert events[0]["payload"].get("changed_fields") == [
        "platform_posts"
    ] or "platform_posts" in events[0]["payload"].get("diff", {})
    session.commit.assert_awaited_once()
