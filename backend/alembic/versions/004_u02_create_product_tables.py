"""U02 - 创建商品 / SKU 基础表 + GIN trgm 索引 + RLS

Revision ID: 004_u02_create_product_tables
Revises: 003_u01_seed_initial_data
Create Date: 2026-05-25 06:30:00.000000

包含内容：
- 启用 pg_trgm 扩展（PG 16 内置 trusted）
- 4 张表：brand / style / sku / style_detail_image
- 12 个索引（含 1 个 GIN trgm 表达式索引 + 部分唯一索引）
- 4 条 RLS 策略
- 追加 brand 相关 permission（U01 seed 未包含）

部分唯一约束（软删后业务键释放）：
- ``uq_style_code`` ON style (tenant_id, style_code) WHERE is_deleted=false
- ``uq_sku_code`` ON sku (tenant_id, sku_code) WHERE is_deleted=false

GIN trgm 索引（U02 强制建，支撑 BR-U02-51 模糊匹配 P95 ≤ 300ms / 5 万行）：
- ``idx_style_search_trgm`` ON style USING gin(
    (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
    gin_trgm_ops
  ) WHERE is_deleted = false
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

# revision identifiers, used by Alembic.
revision: str = "004_u02_create_product_tables"
down_revision: str | Sequence[str] | None = "003_u01_seed_initial_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1) 启用 pg_trgm 扩展
    # ------------------------------------------------------------------ #
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # ------------------------------------------------------------------ #
    # 2) brand 表
    # ------------------------------------------------------------------ #
    op.create_table(
        "brand",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("brand_code", sa.String(32), nullable=False),
        sa.Column("brand_name", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_brand_tenant"
        ),
    )
    op.create_index(
        "uq_brand_code",
        "brand",
        ["tenant_id", "brand_code"],
        unique=True,
    )
    op.create_index(
        "idx_brand_tenant_active",
        "brand",
        ["tenant_id", "is_active"],
    )
    op.execute(enable_rls_sql("brand"))

    # ------------------------------------------------------------------ #
    # 3) style 表
    # ------------------------------------------------------------------ #
    op.create_table(
        "style",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_code", sa.String(64), nullable=False),
        sa.Column("style_name", sa.String(255), nullable=False),
        sa.Column("short_name", sa.String(64), nullable=True),
        sa.Column("brand_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("season", sa.String(16), nullable=True),
        sa.Column("gender", sa.String(8), nullable=True),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tag_color",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("main_image_key", sa.String(512), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "design_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'大货'"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_style_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brand.id"], ondelete="SET NULL", name="fk_style_brand"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["user.id"], ondelete="SET NULL", name="fk_style_owner"
        ),
    )
    # 部分唯一索引：软删后 style_code 释放
    op.create_index(
        "uq_style_code",
        "style",
        ["tenant_id", "style_code"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "idx_style_tenant_active",
        "style",
        ["tenant_id", "is_active", "is_deleted"],
    )
    op.create_index("idx_style_brand", "style", ["tenant_id", "brand_id"])
    op.create_index("idx_style_category", "style", ["tenant_id", "category"])
    op.create_index("idx_style_owner", "style", ["tenant_id", "owner_id"])

    # GIN trgm 索引：表达式索引 + partial WHERE
    # 必须与 service.search_by_keyword 拼接表达式严格一致
    op.execute(
        """
CREATE INDEX idx_style_search_trgm ON style
USING gin (
    (style_code || ' ' || style_name || ' ' || COALESCE(short_name, ''))
    gin_trgm_ops
) WHERE is_deleted = false;
""".strip()
    )

    op.execute(enable_rls_sql("style"))

    # ------------------------------------------------------------------ #
    # 4) sku 表
    # ------------------------------------------------------------------ #
    op.create_table(
        "sku",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_code", sa.String(64), nullable=False),
        sa.Column("color", sa.String(64), nullable=False),
        sa.Column("size", sa.String(32), nullable=False),
        sa.Column("cost_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("purchase_price", sa.Numeric(10, 2), nullable=True),
        sa.Column("base_price", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "sourcing_type",
            sa.String(8),
            nullable=False,
            server_default=sa.text("'自产'"),
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_sku_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["style_id"], ["style.id"], ondelete="RESTRICT", name="fk_sku_style"
        ),
        sa.CheckConstraint(
            "cost_price IS NULL OR cost_price >= 0", name="ck_sku_cost_price_nonneg"
        ),
        sa.CheckConstraint(
            "purchase_price IS NULL OR purchase_price >= 0",
            name="ck_sku_purchase_price_nonneg",
        ),
        sa.CheckConstraint(
            "base_price IS NULL OR base_price >= 0", name="ck_sku_base_price_nonneg"
        ),
    )
    op.create_index(
        "uq_sku_code",
        "sku",
        ["tenant_id", "sku_code"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index("idx_sku_tenant_style", "sku", ["tenant_id", "style_id"])
    op.create_index(
        "idx_sku_tenant_active", "sku", ["tenant_id", "is_active", "is_deleted"]
    )
    op.create_index(
        "idx_sku_style_active",
        "sku",
        ["tenant_id", "style_id", "is_active", "is_deleted"],
    )
    op.execute(enable_rls_sql("sku"))

    # ------------------------------------------------------------------ #
    # 5) style_detail_image 表
    # ------------------------------------------------------------------ #
    op.create_table(
        "style_detail_image",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attachment_key", sa.String(512), nullable=False),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
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
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_sdi_tenant"
        ),
        sa.ForeignKeyConstraint(
            ["style_id"], ["style.id"], ondelete="CASCADE", name="fk_sdi_style"
        ),
    )
    op.create_index(
        "idx_sdi_style", "style_detail_image", ["tenant_id", "style_id", "sort_order"]
    )
    op.execute(enable_rls_sql("style_detail_image"))

    # ------------------------------------------------------------------ #
    # 6) brand permission seed（U01 未包含 brand）
    # ------------------------------------------------------------------ #
    bind = op.get_bind()
    brand_perms = [
        ("brand.*:*", "brand 全部", "function"),
        ("brand.*:read", "brand 只读", "function"),
    ]
    for scope, name, category in brand_perms:
        bind.execute(
            sa.text(
                """
INSERT INTO permission (id, scope, name, category, created_at, updated_at)
VALUES (:id, :scope, :name, :category, NOW(), NOW())
ON CONFLICT (scope) DO NOTHING
"""
            ),
            {"id": uuid4(), "scope": scope, "name": name, "category": category},
        )

    # 给已有角色追加 brand 权限：admin / merchandiser / pr_manager / operations
    # admin 通过 SCOPE_ALL='*' 已覆盖；以下显式追加便于 V1+ 扩展时迁移
    role_brand_grants = [
        ("merchandiser", "brand.*:*"),
        ("pr_manager", "brand.*:read"),
        ("pr", "brand.*:read"),
        ("designer", "brand.*:read"),
        ("design_assistant", "brand.*:read"),
        ("operations", "brand.*:read"),
        ("finance", "brand.*:read"),
        ("pattern_maker", "brand.*:read"),
    ]
    for role_code, scope in role_brand_grants:
        bind.execute(
            sa.text(
                """
INSERT INTO role_permission (id, role_id, permission_id)
SELECT :id, r.id, p.id
FROM role r, permission p
WHERE r.code = :role_code AND p.scope = :scope
ON CONFLICT (role_id, permission_id) DO NOTHING
"""
            ),
            {"id": uuid4(), "role_code": role_code, "scope": scope},
        )


def downgrade() -> None:
    # 反向顺序：先删 RLS，再删表，最后删 brand 权限 seed 与扩展
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
DELETE FROM role_permission
WHERE permission_id IN (
    SELECT id FROM permission WHERE scope IN ('brand.*:*', 'brand.*:read')
)
"""
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM permission WHERE scope IN ('brand.*:*', 'brand.*:read')"
        )
    )

    op.execute(disable_rls_sql("style_detail_image"))
    op.drop_index("idx_sdi_style", table_name="style_detail_image")
    op.drop_table("style_detail_image")

    op.execute(disable_rls_sql("sku"))
    op.drop_index("idx_sku_style_active", table_name="sku")
    op.drop_index("idx_sku_tenant_active", table_name="sku")
    op.drop_index("idx_sku_tenant_style", table_name="sku")
    op.drop_index(
        "uq_sku_code",
        table_name="sku",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.drop_table("sku")

    op.execute(disable_rls_sql("style"))
    op.execute("DROP INDEX IF EXISTS idx_style_search_trgm;")
    op.drop_index("idx_style_owner", table_name="style")
    op.drop_index("idx_style_category", table_name="style")
    op.drop_index("idx_style_brand", table_name="style")
    op.drop_index("idx_style_tenant_active", table_name="style")
    op.drop_index(
        "uq_style_code",
        table_name="style",
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.drop_table("style")

    op.execute(disable_rls_sql("brand"))
    op.drop_index("idx_brand_tenant_active", table_name="brand")
    op.drop_index("uq_brand_code", table_name="brand")
    op.drop_table("brand")

    # 不删除 pg_trgm 扩展（可能其他地方使用）
