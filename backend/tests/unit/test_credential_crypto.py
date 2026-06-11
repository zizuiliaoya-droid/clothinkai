"""U12 单元测试：凭据加解密（crypto.py）+ 失败阈值常量。

覆盖加密往返 / 跨租户密钥不可解 / 密文篡改抛错。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security.crypto import (
    CredentialDecryptError,
    decrypt_credential,
    encrypt_credential,
)
from app.modules.credential.config import CONSECUTIVE_FAILURE_THRESHOLD


class TestCredentialCrypto:
    def test_round_trip(self) -> None:
        tid = uuid4()
        plaintext = "my-secret-password-123"
        ct = encrypt_credential(tid, plaintext)
        assert ct != plaintext.encode()  # 已加密
        out = decrypt_credential(tid, None, ct, purpose="test")
        assert out == plaintext

    def test_cross_tenant_cannot_decrypt(self) -> None:
        tenant_a = uuid4()
        tenant_b = uuid4()
        ct = encrypt_credential(tenant_a, "secret")
        with pytest.raises(CredentialDecryptError):
            decrypt_credential(tenant_b, None, ct, purpose="test")

    def test_tampered_ciphertext_raises(self) -> None:
        tid = uuid4()
        ct = bytearray(encrypt_credential(tid, "secret"))
        ct[-1] ^= 0xFF  # 篡改最后一字节（tag）
        with pytest.raises(CredentialDecryptError):
            decrypt_credential(tid, None, bytes(ct), purpose="test")

    def test_too_short_ciphertext_raises(self) -> None:
        with pytest.raises(CredentialDecryptError):
            decrypt_credential(uuid4(), None, b"short", purpose="test")

    def test_unicode_password(self) -> None:
        tid = uuid4()
        plaintext = "密码🔐P@ss"
        ct = encrypt_credential(tid, plaintext)
        assert decrypt_credential(tid, None, ct, purpose="test") == plaintext


def test_failure_threshold_constant() -> None:
    assert CONSECUTIVE_FAILURE_THRESHOLD == 3
