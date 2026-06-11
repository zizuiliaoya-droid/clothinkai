"""U16 - 创建 order_adjustment + balance_record 表 + promotion.in_store_order + finance scope seed

Revision ID: 020_u16_order_adjustment_balance
Revises: 019_u15_wecom_alert_tables
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "020_u16_order_adjustment_balance"
down_revision: str | Sequence[str] | None = "019_u15_wecom_alert_tables"
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
        "order_adjustment",
        *_base_cols(),
        sa.Column("order_type", sa.String(8), nullable=False),
        sa.Column("order_date", sa.Date(), nullable=True),
        sa.Column("order_no", sa.String(64), nullable=True),
        sa.Column("blogger_identifier", sa.String(128), nullable=True),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payment_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("payment_proof_attachment_id", postgresql.UUID(as_uuid=True),
                  nullable=True),
        sa.Column("exclude_from_roi", sa.Boolean(), nullable=False,
                  server_default=sa.text("false")),
        sa.Column("status", sa.String(8), nullable=False,
                  server_default=sa.text("'待付款'")),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sku_id"], ["sku.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["payment_proof_attachment_id"], ["attachment.id"],
                                ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="SET NULL"),
        sa.CheckConstraint("amount >= 0", name="ck_order_adjustment_amount_nonneg"),
        sa.CheckConstraint("order_type IN ('拍单','刷单')",
                           name="ck_order_adjustment_type"),
        sa.CheckConstraint("status IN ('待付款','已付款')",
                           name="ck_order_adjustment_status"),
    )
    op.create_index("uq_order_adjustment_promotion", "order_adjustment",
                    ["tenant_id", "promotion_id"], unique=True,
                    postgresql_where=sa.text("promotion_id IS NOT NULL"))
    op.create_index("idx_order_adjustment_type", "order_adjustment",
                    ["tenant_id", "order_type", "order_date"])
    op.create_index("idx_order_adjustment_roi", "order_adjustment",
                    ["tenant_id", "style_id", "exclude_from_roi"])

    op.create_table(
        "balance_record",
        *_base_cols(),
        sa.Column("record_date", sa.Date(), nullable=False),
        sa.Column("record_type", sa.String(16), nullable=False),
        sa.Column("income", sa.Numeric(12, 2), nullable=True),
        sa.Column("expense", sa.Numeric(12, 2), nullable=True),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint("income IS NULL OR income >= 0",
                           name="ck_balance_income_nonneg"),
        sa.CheckConstraint("expense IS NULL OR expense >= 0",
                           name="ck_balance_expense_nonneg"),
    )
    op.create_index("idx_balance_record_tenant_created", "balance_record",
                    ["tenant_id", "created_at"])

    op.add_column("promotion", sa.Column(
        "in_store_order", sa.Boolean(), nullable=False,
        server_default=sa.text("false")))

    op.execute(enable_rls_sql("order_adjustment"))
    op.execute(enable_rls_sql("balance_record"))

    _seed_permissions()


def downgrade() -> None:
    _downgrade_permissions()
    op.execute(disable_rls_sql("balance_record"))
    op.execute(disable_rls_sql("order_adjustment"))
    op.drop_column("promotion", "in_store_order")
    op.drop_table("balance_record")
    op.drop_table("order_adjustment")


_PERMISSIONS = [
    ("finance.order:read", "查询拍单/刷单", "function"),
    ("finance.order:write", "录入拍单/刷单", "function"),
    ("finance.balance:read", "查询余额流水", "function"),
    ("finance.balance:write", "录入余额流水", "function"),
]

_MATRIX = {
    "finance": ["finance.order:read", "finance.order:write",
                "finance.balance:read", "finance.balance:write"],
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
