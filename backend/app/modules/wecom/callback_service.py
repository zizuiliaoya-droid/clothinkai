"""U07 企微回调编排（EP08-S08）。

按 P-U07-05：签名校验 + AES 解密 + 幂等状态推进（仅 created → sent/rejected/failed）。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func

from app.core.audit import AuditService
from app.core.metrics import wecom_callback_total
from app.modules.wecom.client import WecomCrypto
from app.modules.wecom.exceptions import WecomCallbackBadSignatureError
from app.modules.wecom.repository import WecomMessageRepository

_RESULT_MAP = {
    "success": "sent",
    "ok": "sent",
    "reject": "rejected",
    "rejected": "rejected",
    "fail": "failed",
    "failed": "failed",
}


class WecomCallbackService:
    def __init__(self, session) -> None:
        self._s = session
        self._messages = WecomMessageRepository(session)

    def _crypto(self, cfg) -> WecomCrypto:
        return WecomCrypto(cfg.callback_token or "", cfg.callback_aes_key or "")

    async def verify_url(
        self, cfg, *, msg_signature: str, timestamp: str, nonce: str, echostr: str
    ) -> str:
        crypto = self._crypto(cfg)
        if not crypto.verify(msg_signature, timestamp, nonce, echostr):
            await self._audit_invalid()
            raise WecomCallbackBadSignatureError()
        return crypto.decrypt(echostr)

    async def handle(
        self,
        cfg,
        *,
        msg_signature: str,
        timestamp: str,
        nonce: str,
        encrypt: str,
    ) -> str:
        crypto = self._crypto(cfg)
        if not crypto.verify(msg_signature, timestamp, nonce, encrypt):
            await self._audit_invalid()
            wecom_callback_total.labels(result="invalid_signature").inc()
            raise WecomCallbackBadSignatureError()

        payload = crypto.parse_callback(crypto.decrypt(encrypt))
        msgid = payload.get("msgid")
        result = str(payload.get("result", "")).lower()

        msg = (
            await self._messages.find_by_msgid(msgid) if msgid else None
        )
        if msg is None or msg.status != "created":
            wecom_callback_total.labels(result="ignored").inc()
            return "ignored"

        new_status = _RESULT_MAP.get(result, "failed")
        msg.status = new_status
        if new_status == "sent":
            msg.sent_at = func.now()
        await self._s.flush()
        wecom_callback_total.labels(result=new_status).inc()
        return new_status

    async def _audit_invalid(self) -> None:
        await AuditService(self._s).log(
            "wecom.callback.invalid_signature",
            resource="wecom_callback",
            actor_type="anonymous",
        )


__all__ = ["WecomCallbackService"]
