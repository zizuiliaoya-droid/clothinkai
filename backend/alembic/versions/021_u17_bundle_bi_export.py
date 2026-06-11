"""U17 - 创建 bundle_product + bundle_item + user_preference 表 + scope seed

Revision ID: 021_u17_bundle_bi_export
Revises: 020_u16_order_adjustment_balance
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "021_u17_bundle_bi_export"
down_revision: str | Sequence[str] | None = "020_u16_order_adjustment_balance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_cols() -> list:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
    ]


def upgrade() -> None:
    op.create_table(
        "bundle_product",
        *_base_cols(),
        sa.Column("bundle_code", sa.String(64), nullable=False),
        sa.Column("bundle_name", sa.String(255), nullable=False),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
    )
    op.create_index("uq_bundle_product_code", "bundle_product",
                    ["tenant_id", "bundle_code"], unique=True)

    op.create_table(
        "bundle_item",
        *_base_cols(),
        sa.Column("bundle_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["bundle_id"], ["bundle_product.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("quantity >= 1", name="ck_bundle_item_quantity_pos"),
    )
    op.create_index("uq_bundle_item_sku", "bundle_item",
                    ["tenant_id", "bundle_id", "sku_id"], unique=True)
    op.create_index("idx_bundle_item_bundle", "bundle_item",
                    ["tenant_id", "bundle_id"])

    op.create_table(
        "user_preference",
        *_base_cols(),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pref_key", sa.String(64), nullable=False),
        sa.Column("pref_value", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
    )
    op.create_index("uq_user_preference", "user_preference",
                    ["tenant_id", "user_id", "pref_key"], unique=True)

    op.execute(enable_rls_sql("bundle_product"))
    op.execute(enable_rls_sql("bundle_item"))
    op.execute(enable_rls_sql("user_preference"))

    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    op.execute(disable_rls_sql("user_preference"))
    op.execute(disable_rls_sql("bundle_item"))
    op.execute(disable_rls_sql("bundle_product"))
    op.drop_table("user_preference")
    op.drop_table("bundle_item")
    op.drop_table("bundle_product")


_PERMISSIONS = [
    ("product.bundle:read", "查询套装/组合商品", "function"),
    ("product.bundle:write", "创建/编辑套装", "function"),
    ("report.export:read", "导出报表 Excel", "function"),
]

_MATRIX = {
    "merchandiser": ["product.bundle:read", "product.bundle:write"],
    "pr_manager": ["report.export:read"],
    "operations": ["report.export:read"],
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
