"""U07 集成测试：企微配置（EP08-S02）—— secret 加密落库不回显 + test_connection。"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text

from app.core.security.crypto import decrypt_credential
from app.core.tenancy import tenant_id_ctx
from app.modules.wecom.client import WecomClient
from app.modules.wecom.config_service import WecomConfigService
from app.modules.wecom.schemas import WecomConfigUpdate


def _payload() -> WecomConfigUpdate:
    return WecomConfigUpdate(
        corp_id="corp123",
        agent_id="1000002",
        secret="super-secret-value",
        callback_token="tok",
        callback_aes_key="k" * 43,
        default_sender_userid="zhangsan",
        is_active=True,
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestWecomConfig:
    async def test_configure_encrypts_and_no_plaintext(
        self, session: Any, tenant_a: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            svc = WecomConfigService(session)
            await svc.configure(_payload(), tenant_a.id)
            await session.flush()

            row = (
                await session.execute(
                    text(
                        "SELECT secret_ciphertext FROM wecom_config "
                        "WHERE tenant_id = :t"
                    ),
                    {"t": str(tenant_a.id)},
                )
            ).first()
            assert row is not None
            ciphertext = bytes(row[0])
            assert ciphertext != b"super-secret-value"
            assert (
                decrypt_credential(
                    tenant_a.id, None, ciphertext, purpose="t"
                )
                == "super-secret-value"
            )

            resp = await svc.get_response()
            assert resp is not None
            assert resp.secret_configured is True
            assert not hasattr(resp, "secret")
        finally:
            tenant_id_ctx.reset(tok)

    async def test_test_connection_ok(
        self, session: Any, tenant_a: Any, monkeypatch
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            svc = WecomConfigService(session)
            await svc.configure(_payload(), tenant_a.id)
            await session.flush()

            async def _fake_token(self, *, force_refresh: bool = False) -> str:
                return "fake-token"

            monkeypatch.setattr(WecomClient, "get_access_token", _fake_token)
            result = await svc.test_connection(tenant_a.id)
            assert result.ok is True
        finally:
            tenant_id_ctx.reset(tok)

    async def test_update_overwrites(
        self, session: Any, tenant_a: Any
    ) -> None:
        tok = tenant_id_ctx.set(tenant_a.id)
        try:
            svc = WecomConfigService(session)
            await svc.configure(_payload(), tenant_a.id)
            await session.flush()
            p2 = _payload()
            p2.corp_id = "corp999"
            await svc.configure(p2, tenant_a.id)
            await session.flush()
            resp = await svc.get_response()
            assert resp.corp_id == "corp999"
            count = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM wecom_config WHERE tenant_id = :t"
                    ),
                    {"t": str(tenant_a.id)},
                )
            ).scalar_one()
            assert count == 1
        finally:
            tenant_id_ctx.reset(tok)
