"""商品主数据 ORM（GoodsMain + GoodsStyleItem），对齐 PRD V1.4 第 3 章。

分层与现有 ``style`` / ``sku`` 的关系：

- ``GoodsMain`` = **销售单元**（PRD 的 ``tb_goods_main``）。一行代表店铺里卖的一个东西，
  单件商品或套装都是它；``is_suit`` 区分。前端只展示 ``goods_title``。
- ``GoodsStyleItem`` = **商品与款式的关联**（PRD 的 ``tb_style_item``）。套装在这里挂多行，
  每行带该款在该商品下的单件货品成本。
- ``Style`` 继续做款式（货品）主数据，不再兼任销售链接层。
- 千牛商品ID 落在 ``platform_product``（已有表，加 ``goods_main_id`` + ``channel``），
  一个商品可以挂多条链接 —— 普通渠道与直播渠道各一条，用于分账。

为什么用关联表而不是给 ``style`` 加一个 ``goods_main_id``：生产数据里两个方向都存在。
``platform_product`` 已有 8 个款式挂着多个千牛ID（一款多链接），而套装是多款共用一个
千牛ID（多款一链接）。只有多对多能同时容纳这两种情况。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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

from app.core.db import TenantScopedModel


class GoodsMain(TenantScopedModel):
    """商品 / 套装（销售单元）。"""

    __tablename__ = "goods_main"

    goods_code: Mapped[str] = mapped_column(String(64), nullable=False)
    """商品编码（租户内唯一）。PRD 的主键是千牛ID，但一个商品要挂多条链接，
    千牛ID 只能落在链接表，所以这里另给一个稳定的业务编码用于引用与去重。"""

    goods_title: Mapped[str] = mapped_column(String(512), nullable=False)
    """商品/套装名称。前端所有下拉与列表展示这个字段，千牛ID 不外露。"""

    main_image_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    season: Mapped[str | None] = mapped_column(String(64), nullable=True)
    brand_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("brand.id", ondelete="SET NULL"), nullable=True
    )

    is_suit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    """是否套装。为 true 时样品成本取关联款式的成本之和。"""

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    """软删除（PRD 改动 1：允许删除但不物理删历史数据）。"""

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    """删除留痕：PRD 要求记录谁删、何时删、删了哪个商品。"""

    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("uq_goods_main_code", "tenant_id", "goods_code", unique=True),
        Index("idx_goods_main_active", "tenant_id", "is_active", "is_deleted"),
        Index("idx_goods_main_season", "tenant_id", "season"),
        Index("idx_goods_main_category", "tenant_id", "category"),
    )


class GoodsStyleItem(TenantScopedModel):
    """商品 ↔ 款式关联（套装明细）。"""

    __tablename__ = "goods_style_item"

    goods_main_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("goods_main.id", ondelete="CASCADE"),
        nullable=False,
    )
    style_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("style.id", ondelete="RESTRICT"),
        nullable=False,
    )

    single_goods_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    """该款在该商品下的单件货品成本。

    落在关联行而不是 ``style`` 上：同一件衣服进不同套装时，业务上允许按不同成本核算
    （例如尾货拼套装）。取值来源是款式的 SKU 成本价，迁移时回填。
    """

    sort_order: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    """停用的关联行不参与套装成本汇总（PRD：只统计 is_enable=1 的子表记录）。"""

    __table_args__ = (
        Index(
            "uq_goods_style_item",
            "tenant_id",
            "goods_main_id",
            "style_id",
            unique=True,
        ),
        Index("idx_goods_style_item_goods", "tenant_id", "goods_main_id", "is_active"),
        Index("idx_goods_style_item_style", "tenant_id", "style_id"),
        CheckConstraint(
            "single_goods_cost IS NULL OR single_goods_cost >= 0",
            name="ck_goods_style_item_cost_nonneg",
        ),
    )


__all__ = ["GoodsMain", "GoodsStyleItem"]
