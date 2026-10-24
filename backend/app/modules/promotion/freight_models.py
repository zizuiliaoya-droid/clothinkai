"""R33 ORM；只映射065已创建的表，不创建第二条付款路径或迁移。"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core import attachment as _attachment  # noqa: F401
from app.core.db import TenantScopedModel
from app.modules.auth import models as _auth  # noqa: F401
from app.modules.blogger import models as _blogger  # noqa: F401
from app.modules.promotion import models as _promotion  # noqa: F401


class ReturnFreightPayment(TenantScopedModel):
    __tablename__ = "return_freight_payment"

    promotion_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("promotion.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(8), server_default=text("'待审核'"))
    payment_qr_attachment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("attachment.id", ondelete="RESTRICT")
    )
    waybill_attachment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("attachment.id", ondelete="RESTRICT")
    )
    submitted_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    rejected_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_date: Mapped[date | None] = mapped_column(Date)
    payment_proof_attachment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("attachment.id", ondelete="RESTRICT")
    )
    paid_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL")
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remark: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_return_freight_amount_pos"),
        CheckConstraint(
            "status IN ('待审核', '待付款', '已付款', '已驳回')", name="ck_return_freight_status"
        ),
        CheckConstraint(
            "status <> '已付款' OR (payment_date IS NOT NULL AND payment_proof_attachment_id IS NOT NULL)",
            name="ck_return_freight_paid_fields",
        ),
        Index("uq_return_freight_promotion", "tenant_id", "promotion_id", unique=True),
        Index("idx_return_freight_status", "tenant_id", "status", submitted_at.desc()),
    )
