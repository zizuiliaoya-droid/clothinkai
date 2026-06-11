"""U07 企微配置编排（EP08-S02）。

- configure：secret AES-256-GCM 加密落库（不回显）
- test_connection：解密 + 调 get_access_token（业务结果，不抛 5xx）
- get_response：仅返回 secret_configured: bool
"""

from __future__ import annotations

from app.core.audit import AuditService
from app.core.security.crypto import decrypt_credential, encrypt_credential
from app.modules.wecom.client import WecomClient, build_http_client
from app.modules.wecom.exceptions import WecomNotConfiguredError
from app.modules.wecom.models import WecomConfig
from app.modules.wecom.repository import WecomConfigRepository
from app.modules.wecom.schemas import (
    WecomConfigResponse,
    WecomConfigUpdate,
    WecomTestResult,
)


class WecomConfigService:
    def __init__(self, session) -> None:
        self._s = session
        self._repo = WecomConfigRepository(session)

    async def configure(self, payload: WecomConfigUpdate, tenant_id) -> WecomConfig:
        cfg = await self._repo.get()
        ciphertext = encrypt_credential(tenant_id, payload.secret)
        if cfg is None:
            cfg = WecomConfig(
                corp_id=payload.corp_id,
                agent_id=payload.agent_id,
                secret_ciphertext=ciphertext,
                callback_token=payload.callback_token,
                callback_aes_key=payload.callback_aes_key,
                default_sender_userid=payload.default_sender_userid,
                is_active=payload.is_active,
            )
            self._repo.add(cfg)
        else:
            cfg.corp_id = payload.corp_id
            cfg.agent_id = payload.agent_id
            cfg.secret_ciphertext = ciphertext
            cfg.callback_token = payload.callback_token
            cfg.callback_aes_key = payload.callback_aes_key
            cfg.default_sender_userid = payload.default_sender_userid
            cfg.is_active = payload.is_active
        await AuditService(self._s).log(
            "wecom.config.update", resource="wecom_config"
        )
        await self._s.flush()
        return cfg

    async def get_response(self) -> WecomConfigResponse | None:
        cfg = await self._repo.get()
        if cfg is None:
            return None
        return WecomConfigResponse(
            corp_id=cfg.corp_id,
            agent_id=cfg.agent_id,
            secret_configured=bool(cfg.secret_ciphertext),
            callback_token=cfg.callback_token,
            default_sender_userid=cfg.default_sender_userid,
            is_active=cfg.is_active,
        )

    async def test_connection(self, tenant_id) -> WecomTestResult:
        cfg = await self._repo.get()
        if cfg is None or not cfg.is_active:
            raise WecomNotConfiguredError()

        async def _secret() -> str:
            await AuditService(self._s).log(
                "wecom.secret.decrypt", resource="wecom_config",
                actor_type="system", purpose="test_connection",
            )
            return decrypt_credential(
                tenant_id, cfg.id, cfg.secret_ciphertext, purpose="test_connection"
            )

        http = build_http_client()
        try:
            client = WecomClient(tenant_id, cfg, http=http, secret_provider=_secret)
            await client.get_access_token(force_refresh=True)
            return WecomTestResult(ok=True)
        except Exception as exc:  # noqa: BLE001 — 连接性测试属业务结果
            return WecomTestResult(ok=False, reason=str(exc))
        finally:
            await http.aclose()


__all__ = ["WecomConfigService"]
