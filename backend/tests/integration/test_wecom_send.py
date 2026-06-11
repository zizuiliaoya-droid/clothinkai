"""U07 集成测试：群发执行 + 频控降级（EP08-S06/S07）。"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.core.tenancy import tenant_id_ctx
from app.modules.wecom.client import WecomClient
from app.modules.wecom.config_service import WecomConfigService
from app.modules.wecom.models import WecomMessage
from app.modules.wecom.schemas import WecomConfigUpdate
from app.modules.wecom.send_service import WecomSendService


async def _seed_config(session, tenant_id) -> None:
    await WecomConfigService(session).configure(
        WecomConfigUpdate(
            corp_id="c", agent_id="1", secret="s",
            default_sender_userid="zs", is_active=True,
        ),
        tenant_id,
    )
    await session.flush()


def _msg(tenant_id, blogger_id, *, pr_id=None, status="pending") -> WecomMessage:
    return WecomMessage(
        tenant_id=tenant_id,
        blogger_id=blogger_id,
        pr_id=pr_id,
        external_userid="ext_1",
        template_type="urge",
        rendered_content="催发内容",
        promotion_ids=[],
        status=status,
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestWecomSend:
    async def test_send_success_created(
        self, session: Any, tenant_a: Any, blogger_factory: Any, monkeypatch
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_config(session, tenant_a.id)
            blogger = await blogger_factory.blogger()
            msg = _msg(tenant_a.id, blogger.id)
            session.add(msg)
            await session.flush()

            async def _fake_send(self, *, sender, recipients, content):
                return {"msgid": "MSG-1"}

            monkeypatch.setattr(
                WecomClient, "send_external_msg_template", _fake_send
            )
            result = await WecomSendService(session).send(msg.id, tenant_a.id)
            assert result["status"] == "created"
            await session.flush()
            assert msg.status == "created"
            assert msg.wecom_msgid == "MSG-1"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_blogger_rate_limit_degrades(
        self, session: Any, tenant_a: Any, blogger_factory: Any, monkeypatch
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_config(session, tenant_a.id)
            blogger = await blogger_factory.blogger(nickname="小美")
            # 当天已有一条 sent（触发博主频控）；pending.pr_id=None → 降级不写通知
            session.add(_msg(tenant_a.id, blogger.id, status="sent"))
            await session.flush()
            pending = _msg(tenant_a.id, blogger.id)
            session.add(pending)
            await session.flush()

            called = {"sent": False}

            async def _fake_send(self, **kw):
                called["sent"] = True
                return {"msgid": "X"}

            monkeypatch.setattr(
                WecomClient, "send_external_msg_template", _fake_send
            )
            result = await WecomSendService(session).send(
                pending.id, tenant_a.id
            )
            assert result["status"] == "rate_limited"
            assert called["sent"] is False  # 未调企微
            await session.flush()
            assert pending.status == "rate_limited"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_send_skips_non_pending(
        self, session: Any, tenant_a: Any, blogger_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_config(session, tenant_a.id)
            blogger = await blogger_factory.blogger()
            msg = _msg(tenant_a.id, blogger.id, status="sent")
            session.add(msg)
            await session.flush()
            result = await WecomSendService(session).send(msg.id, tenant_a.id)
            assert result["status"] == "skipped"
        finally:
            tenant_id_ctx.reset(tok)
