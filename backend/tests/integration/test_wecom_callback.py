"""U07 集成测试：企微回调（EP08-S08）—— 签名校验 + 幂等状态推进。"""

from __future__ import annotations

import base64
import json
import os
from typing import Any

import pytest

from app.core.tenancy import tenant_id_ctx
from app.modules.wecom.callback_service import WecomCallbackService
from app.modules.wecom.client import WecomCrypto
from app.modules.wecom.config_service import WecomConfigService
from app.modules.wecom.exceptions import WecomCallbackBadSignatureError
from app.modules.wecom.models import WecomMessage
from app.modules.wecom.schemas import WecomConfigUpdate

_TOKEN = "tok123"
_AES_KEY = base64.b64encode(os.urandom(32)).decode()[:43]


async def _seed_config(session, tenant_id):
    svc = WecomConfigService(session)
    await svc.configure(
        WecomConfigUpdate(
            corp_id="c", agent_id="1", secret="s",
            callback_token=_TOKEN, callback_aes_key=_AES_KEY,
            default_sender_userid="zs", is_active=True,
        ),
        tenant_id,
    )
    await session.flush()
    return await svc._repo.get()


def _encrypt_payload(msgid: str, result: str) -> tuple[str, str, str, str]:
    crypto = WecomCrypto(_TOKEN, _AES_KEY)
    encrypt = crypto.encrypt(json.dumps({"msgid": msgid, "result": result}))
    timestamp, nonce = "1700000000", "abc123"
    sig = crypto.signature(timestamp, nonce, encrypt)
    return sig, timestamp, nonce, encrypt


@pytest.mark.integration
@pytest.mark.asyncio
class TestWecomCallback:
    async def test_callback_success_marks_sent(
        self, session: Any, tenant_a: Any, blogger_factory: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            cfg = await _seed_config(session, tenant_a.id)
            blogger = await blogger_factory.blogger()
            msg = WecomMessage(
                tenant_id=tenant_a.id, blogger_id=blogger.id,
                template_type="urge", rendered_content="x",
                promotion_ids=[], status="created", wecom_msgid="MID-1",
            )
            session.add(msg)
            await session.flush()

            sig, ts, nonce, enc = _encrypt_payload("MID-1", "success")
            result = await WecomCallbackService(session).handle(
                cfg, msg_signature=sig, timestamp=ts, nonce=nonce, encrypt=enc
            )
            assert result == "sent"
            await session.refresh(msg)
            assert msg.status == "sent"
            assert msg.sent_at is not None
        finally:
            tenant_id_ctx.reset(tok)

    async def test_bad_signature_403(
        self, session: Any, tenant_a: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            cfg = await _seed_config(session, tenant_a.id)
            _sig, ts, nonce, enc = _encrypt_payload("MID-2", "success")
            with pytest.raises(WecomCallbackBadSignatureError):
                await WecomCallbackService(session).handle(
                    cfg, msg_signature="deadbeef", timestamp=ts,
                    nonce=nonce, encrypt=enc,
                )
        finally:
            tenant_id_ctx.reset(tok)

    async def test_unknown_msgid_ignored(
        self, session: Any, tenant_a: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            cfg = await _seed_config(session, tenant_a.id)
            sig, ts, nonce, enc = _encrypt_payload("NOPE", "success")
            result = await WecomCallbackService(session).handle(
                cfg, msg_signature=sig, timestamp=ts, nonce=nonce, encrypt=enc
            )
            assert result == "ignored"
        finally:
            tenant_id_ctx.reset(tok)
