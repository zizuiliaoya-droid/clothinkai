"""U10b - 创建 platform_product 表 + product.platform scope seed

Revision ID: 014_u10b_create_platform_product
Revises: 013_u10a_create_design_tables
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "014_u10b_create_platform_product"
down_revision: str | Sequence[str] | None = "013_u10a_create_design_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "platform_product",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("platform_id", sa.String(64), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
                                name="fk_platform_product_tenant"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="RESTRICT",
                                name="fk_platform_product_style"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], ondelete="SET NULL",
                                name="fk_platform_product_sku"),
    )
    op.create_index(
        "uq_platform_product_tenant_plat_platid",
        "platform_product", ["tenant_id", "platform", "platform_id"], unique=True,
    )
    op.create_index(
        "idx_platform_product_style",
        "platform_product", ["tenant_id", "style_id"],
    )
    op.execute(enable_rls_sql("platform_product"))

    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    op.execute(disable_rls_sql("platform_product"))
    op.drop_table("platform_product")


_PERMISSIONS = [
    ("product.platform:read", "查看平台商品映射", "function"),
    ("product.platform:write", "管理平台商品映射", "function"),
]

_MATRIX = {
    "merchandiser": ["product.platform:write"],
    "operations": ["product.platform:read"],
}


def _seed_permissions() -> None:
    bind = op.get_bind()
    for scope, name, category in _PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (:id, :scope, :name, :category, NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"id": str(uuid4()), "scope": scope, "name": name, "category": category},
        )
    for role_code, scope_list in _MATRIX.items():
        for scope in scope_list:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permission (id, role_id, permission_id) "
                    "SELECT :id, r.id, p.id FROM role r, permission p "
                    "WHERE r.code = :role_code AND p.scope = :scope "
                    "ON CONFLICT (role_id, permission_id) DO NOTHING"
                ),
                {"id": str(uuid4()), "role_code": role_code, "scope": scope},
            )


def _downgrade_permissions() -> None:
    bind = op.get_bind()
    scopes = [s for s, _, _ in _PERMISSIONS]
    bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": scopes},
    )
