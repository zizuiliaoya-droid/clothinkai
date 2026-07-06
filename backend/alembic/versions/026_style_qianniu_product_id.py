"""款式 - 增加 qianniu_product_id（千牛商品ID，用于投产/BI 按此 ID 关联千牛日报数据）

Revision ID: 026_style_qianniu
Revises: 025_grant_app_priv
Create Date: 2026-07-05
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "026_style_qianniu"
down_revision: str | Sequence[str] | None = "025_grant_app_priv"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "style",
        sa.Column("qianniu_product_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "idx_style_qianniu",
        "style",
        ["tenant_id", "qianniu_product_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_style_qianniu", table_name="style")
    op.drop_column("style", "qianniu_product_id")
