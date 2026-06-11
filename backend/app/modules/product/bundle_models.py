"""U17 套装/组合商品 ORM（BundleProduct + BundleItem，TenantScopedModel + RLS）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class BundleProduct(TenantScopedModel):
    """套装/组合商品（一 bundle 多 item）。"""

    __tablename__ = "bundle_product"

    bundle_code: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_name: Mapped[str] = mapped_column(String(255), nullable=False)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    __table_args__ = (
        Index("uq_bundle_product_code", "tenant_id", "bundle_code", unique=True),
    )


class BundleItem(TenantScopedModel):
    """套装组合明细（sku × 数量）。"""

    __tablename__ = "bundle_item"

    bundle_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("bundle_product.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sku.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        Index(
            "uq_bundle_item_sku",
            "tenant_id", "bundle_id", "sku_id",
            unique=True,
        ),
        Index("idx_bundle_item_bundle", "tenant_id", "bundle_id"),
        CheckConstraint("quantity >= 1", name="ck_bundle_item_quantity_pos"),
    )


__all__ = ["BundleItem", "BundleProduct"]
