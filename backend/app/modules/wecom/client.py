"""U07 企微 SDK 封装（WecomClient async + WecomCrypto 回调加解密）。

按 nfr-design P-U07-02：
- WecomClient：httpx.AsyncClient；access_token Redis 缓存 7000s；40014/42001 刷新重试一次；
  errcode 频控类 → WecomRateLimited，其余 → WecomApiError。
- WecomCrypto：回调签名校验（sha1 sorted）+ AES-CBC 加解密（EncodingAESKey），企微回调格式
  random(16) || msg_len(4 BE) || msg || receiveid。

测试通过 monkeypatch 替换 WecomClient 方法；WecomCrypto 可真实 round-trip 测。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
from typing import Any
from uuid import UUID

import httpx

from app.core.cache import cache
from app.core.config import settings
from app.core.metrics import wecom_send_duration_seconds
from app.modules.wecom.exceptions import WecomApiError, WecomRateLimited

# 企微频控 / 超频错误码（命中转降级）
_RATE_LIMIT_ERRCODES = {45009, 45047, 82001, 95000}
# access_token 失效错误码（刷新重试一次）
_TOKEN_EXPIRED_ERRCODES = {40014, 42001}


class WecomClient:
    """企微 REST 异步客户端（每租户一实例）。"""

    def __init__(
        self,
        tenant_id: UUID,
        cfg: Any,
        *,
        http: httpx.AsyncClient,
        secret_provider: Any,
    ) -> None:
        self._tid = tenant_id
        self._cfg = cfg
        self._http = http
        self._secret_provider = secret_provider  # async () -> str（解密 + 审计）

    @property
    def _token_key(self) -> str:
        return f"wecom:token:{self._tid}"

    async def get_access_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = await cache.get(self._token_key)
            if cached:
                return cached
        secret = await self._secret_provider()
        resp = await self._http.get(
            f"{settings.WECOM_API_BASE}/cgi-bin/gettoken",
            params={"corpid": self._cfg.corp_id, "corpsecret": secret},
        )
        data = resp.json()
        if data.get("errcode"):
            raise WecomApiError(data["errcode"], data.get("errmsg"))
        token = data["access_token"]
        await cache.set_with_ttl(self._token_key, token, settings.WECOM_TOKEN_TTL)
        return token

    async def _call(self, method: str, path: str, **kw: Any) -> dict:
        token = await self.get_access_token()
        data = (
            await self._http.request(
                method, f"{settings.WECOM_API_BASE}{path}",
                params={"access_token": token}, **kw,
            )
        ).json()
        if data.get("errcode") in _TOKEN_EXPIRED_ERRCODES:
            token = await self.get_access_token(force_refresh=True)
            data = (
                await self._http.request(
                    method, f"{settings.WECOM_API_BASE}{path}",
                    params={"access_token": token}, **kw,
                )
            ).json()
        if data.get("errcode") in _RATE_LIMIT_ERRCODES:
            raise WecomRateLimited(data["errcode"], data.get("errmsg"))
        if data.get("errcode"):
            raise WecomApiError(data["errcode"], data.get("errmsg"))
        return data

    async def find_external_userid_by_wechat(self, wechat: str) -> str | None:
        """按微信号匹配 external_userid（遍历客户列表，简化实现）。"""
        data = await self._call(
            "GET", "/cgi-bin/externalcontact/get_by_user",
            params={"userid": self._cfg.default_sender_userid},
        ) if self._cfg.default_sender_userid else {"external_userid": []}
        # MVP：外部联系人详情匹配 wechat（真实需调 get 详情；此处由 mock 提供）
        for euid in data.get("external_userid", []):
            detail = await self._call(
                "GET", "/cgi-bin/externalcontact/get",
                params={"external_userid": euid},
            )
            contact = detail.get("external_contact", {})
            if contact.get("unionid") == wechat or contact.get("name") == wechat:
                return euid
        return None

    async def send_external_msg_template(
        self, *, sender: str, recipients: list[str], content: str
    ) -> dict:
        with wecom_send_duration_seconds.time():
            return await self._call(
                "POST", "/cgi-bin/externalcontact/add_msg_template",
                json={
                    "chat_type": "single",
                    "external_userid": recipients,
                    "sender": sender,
                    "text": {"content": content},
                },
            )

    async def send_group_robot(self, webhook_url: str, markdown: str) -> dict:
        """U15 群机器人（控评群）：直连完整 webhook URL（含 key），无需 access_token。"""
        data = (
            await self._http.post(
                webhook_url,
                json={"msgtype": "markdown", "markdown": {"content": markdown}},
            )
        ).json()
        if data.get("errcode"):
            raise WecomApiError(data["errcode"], data.get("errmsg"))
        return data

    async def send_app_message(self, touser: list[str], markdown: str) -> dict:
        """U15 自建应用推送（异常预警管理群）：复用 _call（token 刷新 + 频控）+ 计时。"""
        with wecom_send_duration_seconds.time():
            return await self._call(
                "POST", "/cgi-bin/message/send",
                json={
                    "touser": "|".join(touser),
                    "agentid": int(self._cfg.agent_id),
                    "msgtype": "markdown",
                    "markdown": {"content": markdown},
                },
            )


class WecomCrypto:
    """企微回调签名校验 + AES-CBC 加解密（EncodingAESKey）。"""

    def __init__(self, token: str, aes_key: str, receiveid: str = "") -> None:
        self._token = token or ""
        self._receiveid = receiveid
        # EncodingAESKey 43 字符 + '=' → base64 → 32 字节
        self._key = base64.b64decode((aes_key or "") + "=")
        self._iv = self._key[:16]

    def signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        parts = sorted([self._token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(parts).encode()).hexdigest()

    def verify(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> bool:
        return hmac.compare_digest(
            self.signature(timestamp, nonce, encrypt), msg_signature or ""
        )

    def encrypt(self, plaintext: str) -> str:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        msg = plaintext.encode()
        rand16 = b"0123456789abcdef"
        raw = rand16 + struct.pack(">I", len(msg)) + msg + self._receiveid.encode()
        pad = 32 - (len(raw) % 32)
        raw += bytes([pad]) * pad
        enc = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).encryptor()
        return base64.b64encode(enc.update(raw) + enc.finalize()).decode()

    def decrypt(self, encrypt: str) -> str:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        dec = Cipher(algorithms.AES(self._key), modes.CBC(self._iv)).decryptor()
        raw = dec.update(base64.b64decode(encrypt)) + dec.finalize()
        raw = raw[: -raw[-1]]  # 去 PKCS7 padding
        msg_len = struct.unpack(">I", raw[16:20])[0]
        return raw[20 : 20 + msg_len].decode()

    @staticmethod
    def parse_callback(plaintext: str) -> dict:
        """解析回调载荷为 {msgid, result}（MVP：JSON；V1 可扩展 XML）。"""
        try:
            return json.loads(plaintext)
        except (ValueError, TypeError):
            return {}


def build_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=settings.WECOM_HTTP_TIMEOUT)


__all__ = ["WecomClient", "WecomCrypto", "build_http_client"]
