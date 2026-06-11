"""U12 凭据 Pydantic Schemas。

CredentialPublic 永不含 password / password_ciphertext（BR-U12-10）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.modules.credential.enums import CredentialPlatform


class CredentialCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    platform: CredentialPlatform
    username: str = Field(..., min_length=1, max_length=128)
    password: SecretStr = Field(..., min_length=1)
    privacy_consent: bool = Field(..., description="必须为 true（隐私确认）")
    remark: str | None = None


class CredentialUpdate(BaseModel):
    """仅允许改密码 / 备注（platform/username 不可改）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    password: SecretStr | None = None
    remark: str | None = None


class CredentialPublic(BaseModel):
    """凭据公开视图——永不含明文密码 / 密文。"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    platform: str
    username: str
    status: str
    consecutive_failures: int
    last_failure_reason: str | None
    last_failure_at: datetime | None
    privacy_consent_at: datetime
    remark: str | None
    created_at: datetime
    updated_at: datetime


class CredentialPage(BaseModel):
    items: list[CredentialPublic]
    total: int
    page: int
    page_size: int


__all__ = [
    "CredentialCreate",
    "CredentialPage",
    "CredentialPublic",
    "CredentialUpdate",
]
