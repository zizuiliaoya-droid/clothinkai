"""博主库 - 增加 crawler_metrics JSONB（灰豚爬虫字段，对齐 final.xlsx 博主库 41 列）

Revision ID: 023_blogger_crawler
Revises: 022_u18_ai_advice_log
Create Date: 2026-06-11
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "023_blogger_crawler"
down_revision: str | Sequence[str] | None = "022_u18_ai_advice_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "blogger",
        sa.Column(
            "crawler_metrics",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("blogger", "crawler_metrics")
