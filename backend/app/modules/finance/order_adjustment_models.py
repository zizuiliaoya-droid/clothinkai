"""U16 ORM 模型：OrderAdjustment（拍单/刷单统一建模）+ BalanceRecord（余额流水）。

继承 TenantScopedModel（U01）+ RLS。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class OrderAdjustment(TenantScopedModel):
    """拍单 / 刷单统一建模（order_type 区分）。"""

    __tablename__ = "order_adjustment"

    order_type: Mapped[str] = mapped_column(String(8), nullable=False)
    order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    order_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    blogger_identifier: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    style_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("style.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sku_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sku.id", ondelete="SET NULL"),
        nullable=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    payment_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_proof_attachment_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("attachment.id", ondelete="RESTRICT"),
        nullable=True,
    )
    exclude_from_roi: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    status: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'待付款'")
    )
    promotion_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("promotion.id", ondelete="SET NULL"),
        nullable=True,
    )
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "uq_order_adjustment_promotion",
            "tenant_id", "promotion_id",
            unique=True,
            postgresql_where=text("promotion_id IS NOT NULL"),
        ),
        Index(
            "idx_order_adjustment_type", "tenant_id", "order_type", "order_date"
        ),
        Index(
            "idx_order_adjustment_roi",
            "tenant_id", "style_id", "exclude_from_roi",
        ),
        CheckConstraint("amount >= 0", name="ck_order_adjustment_amount_nonneg"),
        CheckConstraint(
            "order_type IN ('拍单','刷单')", name="ck_order_adjustment_type"
        ),
        CheckConstraint(
            "status IN ('待付款','已付款')", name="ck_order_adjustment_status"
        ),
    )


class BalanceRecord(TenantScopedModel):
    """余额流水（充值 / 支出 + 自动 balance_after）。"""

    __tablename__ = "balance_record"

    record_date: Mapped[date] = mapped_column(Date, nullable=False)
    record_type: Mapped[str] = mapped_column(String(16), nullable=False)
    income: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    expense: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        Index("idx_balance_record_tenant_created", "tenant_id", "created_at"),
        CheckConstraint(
            "income IS NULL OR income >= 0", name="ck_balance_income_nonneg"
        ),
        CheckConstraint(
            "expense IS NULL OR expense >= 0", name="ck_balance_expense_nonneg"
        ),
    )


__all__ = ["BalanceRecord", "OrderAdjustment"]
