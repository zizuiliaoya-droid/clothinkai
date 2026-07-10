"""博主库 - 增加 等级/分类/对接人(主备)+是否添加成功 字段（对齐 zf 反馈 §5）

Revision ID: 028_blogger_level
Revises: 027_dict_item
Create Date: 2026-07-07
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "028_blogger_level"
down_revision: str | Sequence[str] | None = "027_dict_item"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("blogger", sa.Column("level", sa.String(length=8), nullable=True))
    op.add_column(
        "blogger", sa.Column("content_category", sa.String(length=8), nullable=True)
    )
    op.add_column(
        "blogger", sa.Column("contact_primary", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "blogger",
        sa.Column(
            "contact_primary_added",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "blogger", sa.Column("contact_backup", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "blogger",
        sa.Column(
            "contact_backup_added",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_index("idx_blogger_level", "blogger", ["tenant_id", "level"])


def downgrade() -> None:
    op.drop_index("idx_blogger_level", table_name="blogger")
    op.drop_column("blogger", "contact_backup_added")
    op.drop_column("blogger", "contact_backup")
    op.drop_column("blogger", "contact_primary_added")
    op.drop_column("blogger", "contact_primary")
    op.drop_column("blogger", "content_category")
    op.drop_column("blogger", "level")
