"""U02 ORM 模型：Style / Sku / Brand / StyleDetailImage。

按 functional-design/domain-entities.md §3 定义。

继承 ``TenantScopedModel``：
- 自动 ``id`` (UUID PK)
- 自动 ``tenant_id`` (UUID FK + ORM 钩子注入)
- 自动 ``created_at`` / ``updated_at``
- 启用 RLS（migration 中通过 ``rls.enable_rls_sql`` 配置）

业务键唯一约束：
- ``(tenant_id, style_code) WHERE is_deleted=false``
- ``(tenant_id, sku_code) WHERE is_deleted=false``
- ``(tenant_id, brand_code)``（无 is_deleted）

GIN trgm 索引（U02 强制建，支撑 BR-U02-51 模糊匹配 P95 ≤ 300ms / 5 万行）：
- ``idx_style_search_trgm``：表达式索引
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel

if TYPE_CHECKING:
    from datetime import datetime


# ---------------------------------------------------------------------------
# Brand 字典（每租户自维护品牌列表）
# ---------------------------------------------------------------------------


class Brand(TenantScopedModel):
    """品牌字典。"""

    __tablename__ = "brand"

    brand_code: Mapped[str] = mapped_column(String(32), nullable=False)
    brand_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )

    __table_args__ = (
        Index(
            "uq_brand_code",
            "tenant_id",
            "brand_code",
            unique=True,
        ),
        Index("idx_brand_tenant_active", "tenant_id", "is_active"),
    )


# ---------------------------------------------------------------------------
# Style（款式）
# ---------------------------------------------------------------------------


class Style(TenantScopedModel):
    """款式（业务根表）。"""

    __tablename__ = "style"

    style_code: Mapped[str] = mapped_column(String(64), nullable=False)
    style_name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brand_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("brand.id", ondelete="SET NULL"),
        nullable=True,
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    season: Mapped[str | None] = mapped_column(String(16), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(8), nullable=True)
    tags: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    tag_color: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    main_image_key: Mapped[str | None] = mapped_column(
        String(512), nullable=True
    )
    """R2 对象键（main_image），格式如 ``{tenant_id}/styles/{style_id}/main/{filename}``。

    业务通过 ``AttachmentService.get_public_url(key)`` 解析为公开 URL。
    若为 None 则前端使用占位图。"""
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )
    design_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'大货'")
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )
    is_deleted: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )

    __table_args__ = (
        # 部分唯一索引：软删后 style_code 释放
        Index(
            "uq_style_code",
            "tenant_id",
            "style_code",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "idx_style_tenant_active",
            "tenant_id",
            "is_active",
            "is_deleted",
        ),
        Index("idx_style_brand", "tenant_id", "brand_id"),
        Index("idx_style_category", "tenant_id", "category"),
        Index("idx_style_owner", "tenant_id", "owner_id"),
        # GIN trgm 索引在 alembic migration 中通过 op.execute 创建
        # （SQLAlchemy 表达式索引 + GIN 在某些 SQLAlchemy / PG 版本组合下声明繁琐）
    )


# ---------------------------------------------------------------------------
# Sku（最小销售单元）
# ---------------------------------------------------------------------------


class Sku(TenantScopedModel):
    """SKU。"""

    __tablename__ = "sku"

    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("style.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[str] = mapped_column(String(32), nullable=False)
    cost_price: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    purchase_price: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    base_price: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    tag_price: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    """吊牌价（U10a 跟单填写，S10）。U02 阶段为空，migration 013 追加列。"""
    sourcing_type: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'自产'")
    )
    is_active: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("true")
    )
    is_deleted: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )

    __table_args__ = (
        Index(
            "uq_sku_code",
            "tenant_id",
            "sku_code",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
        Index("idx_sku_tenant_style", "tenant_id", "style_id"),
        Index("idx_sku_tenant_active", "tenant_id", "is_active", "is_deleted"),
        Index(
            "idx_sku_style_active",
            "tenant_id",
            "style_id",
            "is_active",
            "is_deleted",
        ),
        CheckConstraint("cost_price IS NULL OR cost_price >= 0", name="ck_sku_cost_price_nonneg"),
        CheckConstraint(
            "purchase_price IS NULL OR purchase_price >= 0",
            name="ck_sku_purchase_price_nonneg",
        ),
        CheckConstraint(
            "base_price IS NULL OR base_price >= 0",
            name="ck_sku_base_price_nonneg",
        ),
        CheckConstraint(
            "tag_price IS NULL OR tag_price >= 0",
            name="ck_sku_tag_price_nonneg",
        ),
    )


# ---------------------------------------------------------------------------
# StyleDetailImage（款式详情图，顺序敏感）
# ---------------------------------------------------------------------------


class StyleDetailImage(TenantScopedModel):
    """款式详情图。

    删除 style 时级联删除关联（attachment 本身保留，由清理任务回收）。
    """

    __tablename__ = "style_detail_image"

    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("style.id", ondelete="CASCADE"),
        nullable=False,
    )
    attachment_key: Mapped[str] = mapped_column(String(512), nullable=False)
    """R2 对象键，格式 ``{tenant_id}/styles/{style_id}/details/{sort_order}/{filename}``。"""
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    __table_args__ = (
        Index(
            "idx_sdi_style", "tenant_id", "style_id", "sort_order"
        ),
    )


__all__ = [
    "Brand",
    "Sku",
    "Style",
    "StyleDetailImage",
]
