"""U17 用户偏好 ORM（UserPreference，TenantScopedModel + RLS）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class UserPreference(TenantScopedModel):
    """用户偏好（BI 布局等，按 user × pref_key 唯一）。"""

    __tablename__ = "user_preference"

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    pref_key: Mapped[str] = mapped_column(String(64), nullable=False)
    pref_value: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        Index(
            "uq_user_preference",
            "tenant_id", "user_id", "pref_key",
            unique=True,
        ),
    )


__all__ = ["UserPreference"]
