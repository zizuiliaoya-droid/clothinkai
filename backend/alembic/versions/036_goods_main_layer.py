"""商品分层：goods_main + goods_style_item，platform_product 加商品归属与渠道。

对齐 PRD V1.4 第 3 章（商品两张表）与改动 1（直播渠道分账）。

分层结果：
- ``goods_main`` = 销售单元（单件或套装，``is_suit`` 区分），前端只展示 ``goods_title``
- ``goods_style_item`` = 商品 ↔ 款式关联，带该款在该商品下的单件货品成本
- ``platform_product`` 继续承载千牛/万相台链接，新增 ``goods_main_id`` 与 ``channel``
- ``style`` 保持款式主数据，不再兼任销售链接层

采用多对多（关联表）而不是给 style 加一个商品外键：生产数据里两个方向都存在 ——
platform_product 已有 8 个款式挂多个千牛ID（一款多链接），套装则是多款共用一个千牛ID。

本迁移只建结构、不搬数据（数据回填在 037），这样结构变更可以先上线且不改变任何读路径。

Revision ID: 036_goods_main_layer
Revises: 035_backfill_brush_roi
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "036_goods_main_layer"
down_revision: str | Sequence[str] | None = "035_backfill_brush_roi"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RLS_POLICY = """
CREATE POLICY tenant_isolation ON "{table}"
    FOR ALL
    TO clothing_app
    USING (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    )
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    )
"""


def _tenant_scoped_columns() -> list[sa.Column]:
    return [
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(_RLS_POLICY.format(table=table))


def upgrade() -> None:
    # ----------------------------- goods_main ----------------------------- #
    op.create_table(
        "goods_main",
        *_tenant_scoped_columns(),
        sa.Column("goods_code", sa.String(64), nullable=False),
        sa.Column("goods_title", sa.String(512), nullable=False),
        sa.Column("main_image_key", sa.String(512), nullable=True),
        sa.Column("category", sa.String(64), nullable=True),
        sa.Column("season", sa.String(64), nullable=True),
        sa.Column(
            "brand_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("brand.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_suit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "deleted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("remark", sa.Text(), nullable=True),
    )
    op.create_index("uq_goods_main_code", "goods_main", ["tenant_id", "goods_code"], unique=True)
    op.create_index(
        "idx_goods_main_active", "goods_main", ["tenant_id", "is_active", "is_deleted"]
    )
    op.create_index("idx_goods_main_season", "goods_main", ["tenant_id", "season"])
    op.create_index("idx_goods_main_category", "goods_main", ["tenant_id", "category"])
    _enable_rls("goods_main")

    # -------------------------- goods_style_item -------------------------- #
    op.create_table(
        "goods_style_item",
        *_tenant_scoped_columns(),
        sa.Column(
            "goods_main_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("goods_main.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "style_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("style.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("single_goods_cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.CheckConstraint(
            "single_goods_cost IS NULL OR single_goods_cost >= 0",
            name="ck_goods_style_item_cost_nonneg",
        ),
    )
    op.create_index(
        "uq_goods_style_item",
        "goods_style_item",
        ["tenant_id", "goods_main_id", "style_id"],
        unique=True,
    )
    op.create_index(
        "idx_goods_style_item_goods",
        "goods_style_item",
        ["tenant_id", "goods_main_id", "is_active"],
    )
    op.create_index(
        "idx_goods_style_item_style", "goods_style_item", ["tenant_id", "style_id"]
    )
    _enable_rls("goods_style_item")

    # ------------------- platform_product：归属 + 渠道 ------------------- #
    op.add_column(
        "platform_product",
        sa.Column("goods_main_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_platform_product_goods_main",
        "platform_product",
        "goods_main",
        ["goods_main_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "platform_product",
        sa.Column(
            "channel", sa.String(16), nullable=False, server_default=sa.text("'普通'")
        ),
    )
    op.create_check_constraint(
        "ck_platform_product_channel",
        "platform_product",
        "channel IN ('普通','直播')",
    )
    op.create_index(
        "idx_platform_product_goods",
        "platform_product",
        ["tenant_id", "goods_main_id", "channel"],
    )


def downgrade() -> None:
    op.drop_index("idx_platform_product_goods", table_name="platform_product")
    op.drop_constraint("ck_platform_product_channel", "platform_product", type_="check")
    op.drop_column("platform_product", "channel")
    op.drop_constraint("fk_platform_product_goods_main", "platform_product", type_="foreignkey")
    op.drop_column("platform_product", "goods_main_id")

    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "goods_style_item"')
    op.drop_index("idx_goods_style_item_style", table_name="goods_style_item")
    op.drop_index("idx_goods_style_item_goods", table_name="goods_style_item")
    op.drop_index("uq_goods_style_item", table_name="goods_style_item")
    op.drop_table("goods_style_item")

    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "goods_main"')
    op.drop_index("idx_goods_main_category", table_name="goods_main")
    op.drop_index("idx_goods_main_season", table_name="goods_main")
    op.drop_index("idx_goods_main_active", table_name="goods_main")
    op.drop_index("uq_goods_main_code", table_name="goods_main")
    op.drop_table("goods_main")
