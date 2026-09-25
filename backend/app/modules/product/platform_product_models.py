"""U10b 平台商品映射 ORM 模型。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel

# goods_main_id 的外键指向 goods_main；不在这里导入它，SQLAlchemy 配置 mapper 时
# 会因为 metadata 里缺表而抛 NoReferencedTableError。
from app.modules.product.goods_models import GoodsMain  # noqa: F401


class PlatformProduct(TenantScopedModel):
    """平台商品 → 内部款式/SKU 映射。"""

    __tablename__ = "platform_product"

    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_id: Mapped[str] = mapped_column(String(64), nullable=False)
    goods_main_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("goods_main.id", ondelete="RESTRICT"),
        nullable=True,
    )
    """商品（销售单元）归属。报表按它聚合，套装因此天然合成一行。

    可空是为了渐进迁移：回填完成、读路径切换后才会变成事实上的必填。
    """
    channel: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'普通'"))
    """渠道：普通 / 直播。PRD 改动 1 要求直播与普通渠道完全分账，
    同一个商品可以各挂一条链接。"""
    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("style.id", ondelete="RESTRICT"), nullable=False
    )
    """款式归属（历史字段）。商品分层落地后报表不再走它，保留是为了可回滚。"""
    sku_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("sku.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))

    __table_args__ = (
        Index(
            "uq_platform_product_tenant_plat_platid",
            "tenant_id",
            "platform",
            "platform_id",
            unique=True,
        ),
        Index("idx_platform_product_style", "tenant_id", "style_id"),
        Index("idx_platform_product_goods", "tenant_id", "goods_main_id", "channel"),
        CheckConstraint("channel IN ('普通','直播')", name="ck_platform_product_channel"),
    )


__all__ = ["PlatformProduct"]
