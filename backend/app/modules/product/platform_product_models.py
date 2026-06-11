"""U10b 平台商品映射 ORM 模型。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel


class PlatformProduct(TenantScopedModel):
    """平台商品 → 内部款式/SKU 映射。"""

    __tablename__ = "platform_product"

    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_id: Mapped[str] = mapped_column(String(64), nullable=False)
    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("style.id", ondelete="RESTRICT"), nullable=False
    )
    sku_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sku.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (
        Index(
            "uq_platform_product_tenant_plat_platid",
            "tenant_id", "platform", "platform_id",
            unique=True,
        ),
        Index("idx_platform_product_style", "tenant_id", "style_id"),
    )


__all__ = ["PlatformProduct"]
