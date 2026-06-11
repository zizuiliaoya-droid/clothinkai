"""U07 博主企微外部联系人绑定编排（EP08-S03）。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.security.crypto import decrypt_credential
from app.modules.blogger.repository import BloggerRepository
from app.modules.wecom.client import WecomClient, build_http_client
from app.modules.wecom.exceptions import (
    WecomBloggerNoWechatError,
    WecomContactNotFoundError,
    WecomNotConfiguredError,
)
from app.modules.wecom.models import WecomContact
from app.modules.wecom.repository import (
    WecomConfigRepository,
    WecomContactRepository,
)


class WecomBindService:
    def __init__(self, session) -> None:
        self._s = session
        self._contacts = WecomContactRepository(session)
        self._configs = WecomConfigRepository(session)

    async def bind_contact(
        self, blogger_id: UUID, tenant_id: UUID, actor_id: UUID | None
    ) -> WecomContact:
        cfg = await self._configs.get()
        if cfg is None or not cfg.is_active:
            raise WecomNotConfiguredError()

        blogger = await BloggerRepository(self._s).get_by_id(blogger_id)
        if blogger is None or not blogger.wechat:
            raise WecomBloggerNoWechatError()

        async def _secret() -> str:
            return decrypt_credential(
                tenant_id, cfg.id, cfg.secret_ciphertext, purpose="bind"
            )

        http = build_http_client()
        try:
            client = WecomClient(
                tenant_id, cfg, http=http, secret_provider=_secret
            )
            external_userid = await client.find_external_userid_by_wechat(
                blogger.wechat
            )
        finally:
            await http.aclose()

        if not external_userid:
            raise WecomContactNotFoundError()

        contact = await self._contacts.get_by_blogger(blogger_id)
        now = datetime.now(timezone.utc)
        if contact is None:
            contact = WecomContact(
                blogger_id=blogger_id,
                external_userid=external_userid,
                matched_wechat=blogger.wechat,
                bound_by=actor_id,
                bound_at=now,
            )
            self._contacts.add(contact)
        else:
            contact.external_userid = external_userid
            contact.matched_wechat = blogger.wechat
            contact.bound_by = actor_id
            contact.bound_at = now
        await self._s.flush()
        return contact


__all__ = ["WecomBindService"]
