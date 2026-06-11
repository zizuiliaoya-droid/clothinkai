"""U12 平台凭据 ORM 模型。

继承 TenantScopedModel（id/tenant_id/created_at/updated_at + RLS）。
password_ciphertext 存 AES-256-GCM 密文（nonce‖ct‖tag），复用 core/security/crypto.py。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class Credential(TenantScopedModel):
    """平台采集凭据（加密存储）。"""

    __tablename__ = "credential"

    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'paused'")
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    privacy_consent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "uq_credential_tenant_plat_user",
            "tenant_id",
            "platform",
            "username",
            unique=True,
        ),
        Index("idx_credential_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "status IN ('active','paused','disabled')",
            name="ck_credential_status",
        ),
        CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_credential_failures_nonneg",
        ),
    )


__all__ = ["Credential"]
