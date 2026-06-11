"""U03 - 创建博主库基础表 + GIN trgm + GIN JSONB + RLS

Revision ID: 005_u03_create_blogger_table
Revises: 004_u02_create_product_tables
Create Date: 2026-05-26 06:30:00.000000

包含内容：
- 确保 pg_trgm 扩展已启用（U02 migration 004 已创建，幂等）
- 1 张表：blogger
- 10 个索引（含 2 个 GIN trgm 单字段 + 2 个 GIN JSONB + partial UNIQUE）
- 1 条 RLS 策略
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

# revision identifiers, used by Alembic.
revision: str = "005_u03_create_blogger_table"
down_revision: str | Sequence[str] | None = "004_u02_create_product_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) 确保 pg_trgm 扩展已启用（U02 migration 004 已创建，幂等）
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # 2) blogger 表
    op.create_table(
        "blogger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("xiaohongshu_id", sa.String(64), nullable=False),
        sa.Column("nickname", sa.String(128), nullable=False),
        sa.Column(
            "platform",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'小红书'"),
        ),
        sa.Column("wechat", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("follower_count", sa.Integer(), nullable=True),
        sa.Column("blogger_type", sa.String(16), nullable=True),
        sa.Column("gender_target", sa.String(16), nullable=True),
        sa.Column(
            "category_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "quality_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("quote", sa.Numeric(10, 2), nullable=True),
        sa.Column("cooperation_history", sa.Text(), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column(
            "is_suspected_fake",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
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
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_blogger_tenant"
        ),
        sa.CheckConstraint(
            "follower_count IS NULL OR follower_count >= 0",
            name="ck_blogger_follower_count_nonneg",
        ),
        sa.CheckConstraint(
            "quote IS NULL OR quote >= 0", name="ck_blogger_quote_nonneg"
        ),
    )

    # 3) 索引（10 个）
    op.create_index(
        "uq_blogger_xiaohongshu_id",
        "blogger",
        ["tenant_id", "xiaohongshu_id"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "idx_blogger_tenant_active",
        "blogger",
        ["tenant_id", "is_active", "is_deleted"],
    )
    op.create_index("idx_blogger_type", "blogger", ["tenant_id", "blogger_type"])
    op.create_index(
        "idx_blogger_follower_count", "blogger", ["tenant_id", "follower_count"]
    )
    op.create_index("idx_blogger_platform", "blogger", ["tenant_id", "platform"])
    op.create_index(
        "idx_blogger_suspected_fake",
        "blogger",
        ["tenant_id"],
        postgresql_where=sa.text("is_suspected_fake = true"),
    )

    # GIN trgm 单字段索引（U03 数据量小，不需拼接表达式）
    op.execute(
        """
CREATE INDEX idx_blogger_nickname_trgm ON blogger
USING gin (nickname gin_trgm_ops) WHERE is_deleted = false;
""".strip()
    )
    op.execute(
        """
CREATE INDEX idx_blogger_xhs_id_trgm ON blogger
USING gin (xiaohongshu_id gin_trgm_ops) WHERE is_deleted = false;
""".strip()
    )

    # GIN JSONB 索引
    op.execute(
        "CREATE INDEX idx_blogger_category_tags ON blogger USING gin (category_tags);"
    )
    op.execute(
        "CREATE INDEX idx_blogger_quality_tags ON blogger USING gin (quality_tags);"
    )

    # 4) RLS 策略
    op.execute(enable_rls_sql("blogger"))


def downgrade() -> None:
    op.execute(disable_rls_sql("blogger"))

    op.execute("DROP INDEX IF EXISTS idx_blogger_quality_tags;")
    op.execute("DROP INDEX IF EXISTS idx_blogger_category_tags;")
    op.execute("DROP INDEX IF EXISTS idx_blogger_xhs_id_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_blogger_nickname_trgm;")
    op.drop_index(
        "idx_blogger_suspected_fake",
        table_name="blogger",
        postgresql_where=sa.text("is_suspected_fake = true"),
    )
    op.drop_index("idx_blogger_platform", table_name="blogger")
    op.drop_index("idx_blogger_follower_count", table_name="blogger")
    op.drop_index("idx_blogger_type", table_name="blogger")
    op.drop_index("idx_blogger_tenant_active", table_name="blogger")
    op.drop_index(
        "uq_blogger_xiaohongshu_id",
        table_name="blogger",
        postgresql_where=sa.text("is_deleted = false"),
    )

    op.drop_table("blogger")

    # 不删除 pg_trgm 扩展（U02 仍在使用）
