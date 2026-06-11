"""U07 群发执行 + 频控降级编排（EP08-S06/S07，由 Celery 任务逐消息调用）。

按 P-U07-04：每消息独立事务；频控（DB 当天计数）先于发送；命中 → rate_limited + notify。
"""

from __future__ import annotations

from uuid import UUID

from app.core.metrics import (
    wecom_message_total,
    wecom_rate_limited_total,
)
from app.core.security.crypto import decrypt_credential
from app.modules.blogger.repository import BloggerRepository
from app.modules.promotion.urge_calculator import get_today
from app.modules.wecom.client import WecomClient, build_http_client
from app.modules.wecom.exceptions import WecomApiError, WecomRateLimited
from app.modules.wecom.notification_service import NotificationService
from app.modules.wecom.repository import (
    WecomConfigRepository,
    WecomMessageRepository,
)


class WecomSendService:
    def __init__(self, session) -> None:
        self._s = session
        self._messages = WecomMessageRepository(session)
        self._configs = WecomConfigRepository(session)
        self._notify = NotificationService(session)

    async def send(self, message_id: UUID, tenant_id: UUID) -> dict:
        msg = await self._messages.get(message_id)
        if msg is None or msg.status != "pending":
            return {"status": "skipped"}

        today = get_today()
        if (
            await self._messages.count_today_active(
                today=today, blogger_id=msg.blogger_id
            )
            >= 1
        ):
            return await self._degrade(msg, "blogger")
        if msg.pr_id is not None and (
            await self._messages.count_today_active(today=today, pr_id=msg.pr_id)
            >= 1
        ):
            return await self._degrade(msg, "pr")

        cfg = await self._configs.get()
        if cfg is None or not cfg.is_active:
            msg.status = "failed"
            msg.error_detail = "企微未配置"
            wecom_message_total.labels(status="failed").inc()
            return {"status": "failed"}

        async def _secret() -> str:
            return decrypt_credential(
                tenant_id, cfg.id, cfg.secret_ciphertext, purpose="wecom_send"
            )

        http = build_http_client()
        try:
            client = WecomClient(
                tenant_id, cfg, http=http, secret_provider=_secret
            )
            resp = await client.send_external_msg_template(
                sender=cfg.default_sender_userid or "",
                recipients=[msg.external_userid] if msg.external_userid else [],
                content=msg.rendered_content,
            )
            msg.wecom_msgid = resp.get("msgid")
            msg.status = "created"
            wecom_message_total.labels(status="created").inc()
            return {"status": "created"}
        except WecomRateLimited:
            return await self._degrade(msg, "api")
        except WecomApiError as exc:
            msg.status = "failed"
            msg.error_detail = str(exc)
            wecom_message_total.labels(status="failed").inc()
            return {"status": "failed"}
        finally:
            await http.aclose()

    async def _degrade(self, msg, reason: str) -> dict:
        msg.status = "rate_limited"
        msg.error_detail = f"频控降级:{reason}"
        nickname = await self._blogger_name(msg.blogger_id)
        if msg.pr_id is not None:
            await self._notify.notify(
                [msg.pr_id], f"请手动催发 {nickname}"
            )
        wecom_rate_limited_total.labels(reason=reason).inc()
        wecom_message_total.labels(status="rate_limited").inc()
        return {"status": "rate_limited"}

    async def _blogger_name(self, blogger_id: UUID) -> str:
        blogger = await BloggerRepository(self._s).get_by_id(blogger_id)
        return blogger.nickname if blogger else str(blogger_id)


__all__ = ["WecomSendService"]
