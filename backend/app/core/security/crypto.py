"""AES-256-GCM 凭据加解密（U07 落地，U12 追加轮换 + 采集凭据 CRUD）。

设计（U07 nfr-design P-U07-01）：
- 每租户独立密钥 = HKDF-SHA256(master=CREDENTIAL_MASTER_KEY, salt=tenant_id.bytes,
  info=b"wecom-credential")，A 租户密钥无法解 B 密文。
- AES-256-GCM 加密 + 认证标签：密文格式 = ``nonce(12B) || ciphertext || tag(16B)``。
- 解密时 tag 校验失败（篡改 / 密钥不匹配）→ CredentialDecryptError（绝不静默返回空）。
- 密钥每次按需派生（不缓存于进程，降低泄露面；HKDF 开销 < 1ms）。

与 NFR Design 第 4.1 节"凭据加密威胁模型"和 functional-design BR-U07-01~05 一致。
"""

from __future__ import annotations

import base64
import os
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import settings

_NONCE_BYTES = 12
_INFO = b"wecom-credential"


class CredentialEncryptionNotImplemented(NotImplementedError):
    """U07 之前的占位异常（保留以兼容旧引用）。"""


class CredentialDecryptError(Exception):
    """凭据解密失败（密文篡改 / 密钥不匹配 / 格式错误）。"""


def _derive_key(tenant_id: UUID) -> bytes:
    """每租户派生 32 字节 AES 密钥（HKDF-SHA256，salt=tenant_id）。"""
    master = base64.b64decode(settings.CREDENTIAL_MASTER_KEY.get_secret_value())
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=tenant_id.bytes,
        info=_INFO,
    ).derive(master)


def encrypt_credential(tenant_id: UUID, plaintext: str) -> bytes:
    """AES-256-GCM 加密；返回 ``nonce(12) || ciphertext || tag(16)``。"""
    nonce = os.urandom(_NONCE_BYTES)
    ct = AESGCM(_derive_key(tenant_id)).encrypt(nonce, plaintext.encode(), None)
    return nonce + ct


def decrypt_credential(
    tenant_id: UUID,
    _credential_id: UUID | None,
    ciphertext: bytes,
    *,
    purpose: str,
) -> str:
    """解密凭据；tag 校验失败 → CredentialDecryptError（不静默）。

    Args:
        purpose: 解密用途（调用方按需写 ``@audit("...decrypt")``）。
    """
    if not ciphertext or len(ciphertext) <= _NONCE_BYTES:
        raise CredentialDecryptError("密文长度非法")
    nonce, ct = ciphertext[:_NONCE_BYTES], ciphertext[_NONCE_BYTES:]
    try:
        return AESGCM(_derive_key(tenant_id)).decrypt(nonce, ct, None).decode()
    except Exception as exc:  # noqa: BLE001 — InvalidTag 等统一转业务异常
        raise CredentialDecryptError("凭据解密失败（密文损坏或密钥不匹配）") from exc


def rotate_tenant_key(_tenant_id: UUID) -> None:
    """轮换租户密钥（占位，P1+ 实施）。"""
    raise CredentialEncryptionNotImplemented(
        "密钥轮换在 P1+ 阶段实施",
    )


__all__ = [
    "CredentialDecryptError",
    "CredentialEncryptionNotImplemented",
    "decrypt_credential",
    "encrypt_credential",
    "rotate_tenant_key",
]
