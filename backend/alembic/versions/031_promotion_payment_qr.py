"""推广收款码附件 + 收紧 warehouse 为专用打单写权限。

Revision ID: 031_promo_payment_qr
Revises: 030_warehouse_role
Create Date: 2026-07-12
"""
from __future__ import annotations

from typing import Sequence
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "031_promo_payment_qr"
down_revision: str | Sequence[str] | None = "030_warehouse_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "promotion",
        sa.Column("payment_qr_attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_promotion_payment_qr_attachment",
        "promotion", "attachment",
        ["payment_qr_attachment_id"], ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_promotion_payment_qr_attachment_id",
        "promotion", ["payment_qr_attachment_id"],
        postgresql_where=sa.text("payment_qr_attachment_id IS NOT NULL"),
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
            "VALUES (:id, 'promotion.warehouse:write', '仓库回传发货单号', "
            "'function', NOW(), NOW()) ON CONFLICT (scope) DO NOTHING"
        ),
        {"id": str(uuid4())},
    )
    bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE role_id=(SELECT id FROM role WHERE code='warehouse') "
            "AND permission_id=(SELECT id FROM permission WHERE scope='promotion:write')"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO role_permission (id, role_id, permission_id) "
            "SELECT :id, r.id, p.id FROM role r, permission p "
            "WHERE r.code='warehouse' AND p.scope='promotion.warehouse:write' "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"id": str(uuid4())},
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE role_id=(SELECT id FROM role WHERE code='warehouse') "
            "AND permission_id=(SELECT id FROM permission WHERE scope='promotion.warehouse:write')"
        )
    )
    bind.execute(
        sa.text(
            "INSERT INTO role_permission (id, role_id, permission_id) "
            "SELECT :id, r.id, p.id FROM role r, permission p "
            "WHERE r.code='warehouse' AND p.scope='promotion:write' "
            "ON CONFLICT (role_id, permission_id) DO NOTHING"
        ),
        {"id": str(uuid4())},
    )
    bind.execute(sa.text("DELETE FROM permission WHERE scope='promotion.warehouse:write'"))
    op.drop_index("idx_promotion_payment_qr_attachment_id", table_name="promotion")
    op.drop_constraint("fk_promotion_payment_qr_attachment", "promotion", type_="foreignkey")
    op.drop_column("promotion", "payment_qr_attachment_id")
