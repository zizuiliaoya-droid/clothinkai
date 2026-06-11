"""U12 凭据模块异常（继承 core/exceptions 基类）。"""

from __future__ import annotations

from app.core.exceptions import (
    DuplicateResourceError,
    ResourceNotFoundError,
    ValidationError,
)


class PrivacyConsentRequired(ValidationError):
    code = "PRIVACY_CONSENT_REQUIRED"
    message = "请先确认隐私协议"


class CredentialAlreadyExists(DuplicateResourceError):
    code = "CREDENTIAL_ALREADY_EXISTS"
    message = "该平台账号凭据已存在"


class CredentialNotFound(ResourceNotFoundError):
    code = "CREDENTIAL_NOT_FOUND"
    message = "凭据不存在"


__all__ = [
    "CredentialAlreadyExists",
    "CredentialNotFound",
    "PrivacyConsentRequired",
]
