"""U07 凭据加密单元测试（AES-256-GCM + 每租户 HKDF）。"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.security.crypto import (
    CredentialDecryptError,
    decrypt_credential,
    encrypt_credential,
)


def test_encrypt_decrypt_round_trip():
    tid = uuid4()
    ct = encrypt_credential(tid, "my-wecom-secret")
    assert ct != b"my-wecom-secret"
    assert decrypt_credential(tid, None, ct, purpose="test") == "my-wecom-secret"


def test_cross_tenant_key_cannot_decrypt():
    tid_a, tid_b = uuid4(), uuid4()
    ct = encrypt_credential(tid_a, "secret-a")
    with pytest.raises(CredentialDecryptError):
        decrypt_credential(tid_b, None, ct, purpose="test")


def test_tampered_ciphertext_fails():
    tid = uuid4()
    ct = bytearray(encrypt_credential(tid, "secret"))
    ct[-1] ^= 0x01  # 篡改 tag 最后一字节
    with pytest.raises(CredentialDecryptError):
        decrypt_credential(tid, None, bytes(ct), purpose="test")


def test_empty_ciphertext_fails():
    with pytest.raises(CredentialDecryptError):
        decrypt_credential(uuid4(), None, b"", purpose="test")


def test_distinct_nonce_per_encrypt():
    tid = uuid4()
    assert encrypt_credential(tid, "x") != encrypt_credential(tid, "x")
