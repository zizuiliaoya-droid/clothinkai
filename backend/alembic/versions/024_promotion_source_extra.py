"""站外推广 - 增加 source_extra JSONB（人工源列，对齐 final.xlsx 站外推广 41 列）

Revision ID: 024_promo_source
Revises: 023_blogger_crawler
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "024_promo_source"
down_revision: str | Sequence[str] | None = "023_blogger_crawler"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "promotion",
        sa.Column(
            "source_extra",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("promotion", "source_extra")
