"""U07 集成测试：博主外部联系人绑定（EP08-S03）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.tenancy import tenant_id_ctx
from app.modules.wecom.bind_service import WecomBindService
from app.modules.wecom.client import WecomClient
from app.modules.wecom.config_service import WecomConfigService
from app.modules.wecom.exceptions import (
    WecomBloggerNoWechatError,
    WecomContactNotFoundError,
    WecomNotConfiguredError,
)
from app.modules.wecom.schemas import WecomConfigUpdate


async def _seed_config(session, tenant_id) -> None:
    await WecomConfigService(session).configure(
        WecomConfigUpdate(
            corp_id="c", agent_id="1", secret="s",
            default_sender_userid="zs", is_active=True,
        ),
        tenant_id,
    )
    await session.flush()


@pytest.mark.integration
@pytest.mark.asyncio
class TestWecomBind:
    async def test_bind_success(
        self, session: Any, tenant_a: Any, blogger_factory: Any, monkeypatch
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_config(session, tenant_a.id)
            blogger = await blogger_factory.blogger(wechat="wx_xiaomei")

            async def _fake_find(self, wechat: str):
                return "ext_userid_123"

            monkeypatch.setattr(
                WecomClient, "find_external_userid_by_wechat", _fake_find
            )
            contact = await WecomBindService(session).bind_contact(
                blogger.id, tenant_a.id, None
            )
            assert contact.external_userid == "ext_userid_123"
            assert contact.matched_wechat == "wx_xiaomei"
        finally:
            tenant_id_ctx.reset(tok)

    async def test_bind_no_wechat_422(
        self, session: Any, tenant_a: Any, blogger_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_config(session, tenant_a.id)
            blogger = await blogger_factory.blogger(wechat=None)
            with pytest.raises(WecomBloggerNoWechatError):
                await WecomBindService(session).bind_contact(
                    blogger.id, tenant_a.id, None
                )
        finally:
            tenant_id_ctx.reset(tok)

    async def test_bind_not_matched_404(
        self, session: Any, tenant_a: Any, blogger_factory: Any, monkeypatch
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            await _seed_config(session, tenant_a.id)
            blogger = await blogger_factory.blogger(wechat="wx_unknown")

            async def _fake_find(self, wechat: str):
                return None

            monkeypatch.setattr(
                WecomClient, "find_external_userid_by_wechat", _fake_find
            )
            with pytest.raises(WecomContactNotFoundError):
                await WecomBindService(session).bind_contact(
                    blogger.id, tenant_a.id, None
                )
        finally:
            tenant_id_ctx.reset(tok)

    async def test_bind_not_configured_409(
        self, session: Any, tenant_a: Any, blogger_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            blogger = await blogger_factory.blogger(wechat="wx_x")
            with pytest.raises(WecomNotConfiguredError):
                await WecomBindService(session).bind_contact(
                    blogger.id, tenant_a.id, None
                )
        finally:
            tenant_id_ctx.reset(tok)
