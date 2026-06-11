"""U18 AI 调用留痕 ORM（AiAdviceLog，TenantScopedModel + RLS）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class AiAdviceLog(TenantScopedModel):
    """AI 请求/响应留痕（成功/降级/失败均落一条）。"""

    __tablename__ = "ai_advice_log"

    advice_type: Mapped[str] = mapped_column(String(16), nullable=False)
    request_payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(8), nullable=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    model: Mapped[str | None] = mapped_column(String(32), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index(
            "idx_ai_advice_log_tenant_type",
            "tenant_id", "advice_type", "created_at",
        ),
    )


__all__ = ["AiAdviceLog"]
