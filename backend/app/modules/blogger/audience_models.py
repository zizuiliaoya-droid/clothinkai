"""四维画像采集记录；当前分布复用 blogger.audience_profile。"""

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Date, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class BloggerAudienceCapture(TenantScopedModel):
    __tablename__ = "blogger_audience_capture"
    blogger_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    collected_on: Mapped[date] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    estimated: Mapped[bool] = mapped_column(nullable=False)
    tgi_source: Mapped[str | None] = mapped_column(String(256))
    entries: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    import_batch_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("import_batch.id", ondelete="RESTRICT")
    )
    row_numbers: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "blogger_id"],
            ["blogger.tenant_id", "blogger.id"],
            name="fk_audience_blogger_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["user.tenant_id", "user.id"],
            name="fk_audience_actor_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_blogger_audience_capture",
            "tenant_id",
            "blogger_id",
            "dimension",
            "collected_on",
            unique=True,
        ),
    )
