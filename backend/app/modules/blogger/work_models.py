"""博主作品的逐次采集快照；不影响推广发布记录。"""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class BloggerWorkSnapshot(TenantScopedModel):
    __tablename__ = "blogger_work_snapshot"

    blogger_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    work_key: Mapped[str] = mapped_column(String(68), nullable=False)
    work_id: Mapped[str | None] = mapped_column(String(128))
    work_url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str | None] = mapped_column(String(500))
    published_on: Mapped[date] = mapped_column(Date, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    like_count: Mapped[int | None] = mapped_column(BigInteger)
    comment_count: Mapped[int | None] = mapped_column(BigInteger)
    share_count: Mapped[int | None] = mapped_column(BigInteger)
    collect_count: Mapped[int | None] = mapped_column(BigInteger)
    play_count: Mapped[int | None] = mapped_column(BigInteger)
    import_batch_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("import_batch.id", ondelete="RESTRICT")
    )
    row_number: Mapped[int | None]
    created_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "blogger_id"],
            ["blogger.tenant_id", "blogger.id"],
            name="fk_work_blogger_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["user.tenant_id", "user.id"],
            name="fk_work_actor_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_blogger_work_snapshot",
            "tenant_id",
            "blogger_id",
            "work_key",
            "collected_at",
            unique=True,
        ),
    )
