"""刷单/拍单收款码附件。

刷单与拍单合并为一个页面后，给博主打款同样需要收款码。站外推广已有
``promotion.payment_qr_attachment_id``，这里按同一模式给 ``order_adjustment`` 补上：
私有桶 + RESTRICT（附件被引用时不允许直接删）+ 仅非空行进索引。

写权限复用 ``finance.order:write``，不另开 scope —— 这里没有站外推广那种
「仓库角色只能回传单号、不能看收款码」的拆分需求。

Revision ID: 033_order_payment_qr
Revises: 032_promo_warehouse_idx
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "033_order_payment_qr"
down_revision: str | Sequence[str] | None = "032_promo_warehouse_idx"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "order_adjustment",
        sa.Column("payment_qr_attachment_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_order_adjustment_payment_qr_attachment",
        "order_adjustment",
        "attachment",
        ["payment_qr_attachment_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "idx_order_adjustment_payment_qr",
        "order_adjustment",
        ["payment_qr_attachment_id"],
        postgresql_where=sa.text("payment_qr_attachment_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_order_adjustment_payment_qr", table_name="order_adjustment")
    op.drop_constraint(
        "fk_order_adjustment_payment_qr_attachment",
        "order_adjustment",
        type_="foreignkey",
    )
    op.drop_column("order_adjustment", "payment_qr_attachment_id")
