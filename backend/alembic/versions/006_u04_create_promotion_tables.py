"""U04 - 创建推广合作核心表 + GIN trgm + RLS

Revision ID: 006_u04_create_promotion_tables
Revises: 005_u03_create_blogger_table
Create Date: 2026-05-26 09:00:00.000000

包含内容：
- 确保 pg_trgm 扩展已启用（U02 migration 004 已创建，幂等）
- 2 张表：promotion / promotion_sequence
- 11 个 promotion 索引（含 3 个 GIN trgm + partial UNIQUE on internal_code）
- 1 个 promotion_sequence 唯一索引
- 4 条 CHECK 约束 + 1 条 promotion_sequence CHECK
- 2 条 RLS 策略
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

# revision identifiers, used by Alembic.
revision: str = "006_u04_create_promotion_tables"
down_revision: str | Sequence[str] | None = "005_u03_create_blogger_table"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1) pg_trgm 扩展（幂等）
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # ========================================================================
    # 2) promotion 表
    # ========================================================================
    op.create_table(
        "promotion",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        # 关联
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=True),
        # 业务键
        sa.Column("internal_code", sa.String(64), nullable=False),
        # 快照字段
        sa.Column("style_code_snapshot", sa.String(64), nullable=False),
        sa.Column("style_short_name_snapshot", sa.String(128), nullable=False),
        sa.Column("quote_amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("cost_snapshot", sa.Numeric(10, 2), nullable=True),
        # 业务字段
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("cooperation_date", sa.Date(), nullable=False),
        sa.Column("scheduled_publish_date", sa.Date(), nullable=True),
        sa.Column("actual_publish_date", sa.Date(), nullable=True),
        sa.Column("publish_url", sa.String(512), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("recall_reason", sa.Text(), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=True),
        sa.Column("note_title", sa.String(255), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        # 三个状态字段
        sa.Column(
            "publish_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'未发布'"),
        ),
        sa.Column(
            "recall_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'未召回'"),
        ),
        sa.Column(
            "settlement_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'未核查'"),
        ),
        # 审核
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_action", sa.String(16), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        # 通用
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
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
        # FK 约束
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_promotion_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["style_id"], ["style.id"], ondelete="RESTRICT",
            name="fk_promotion_style",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"], ["sku.id"], ondelete="RESTRICT",
            name="fk_promotion_sku",
        ),
        sa.ForeignKeyConstraint(
            ["blogger_id"], ["blogger.id"], ondelete="RESTRICT",
            name="fk_promotion_blogger",
        ),
        sa.ForeignKeyConstraint(
            ["pr_id"], ["user.id"], ondelete="SET NULL",
            name="fk_promotion_pr",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["user.id"], ondelete="SET NULL",
            name="fk_promotion_reviewer",
        ),
        # CHECK 约束
        sa.CheckConstraint(
            "like_count IS NULL OR like_count >= 0",
            name="ck_promotion_like_count_nonneg",
        ),
        sa.CheckConstraint(
            "quote_amount >= 0",
            name="ck_promotion_quote_amount_nonneg",
        ),
        sa.CheckConstraint(
            "cost_snapshot IS NULL OR cost_snapshot >= 0",
            name="ck_promotion_cost_snapshot_nonneg",
        ),
    )

    # ========================================================================
    # 3) promotion 索引（11 个 + 3 GIN trgm）
    # ========================================================================
    # 业务键唯一（part：仅 active 行参与）
    op.create_index(
        "uq_promotion_internal_code",
        "promotion",
        ["tenant_id", "internal_code"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )
    # 列表过滤
    op.create_index(
        "idx_promotion_tenant_active",
        "promotion",
        ["tenant_id", "is_active", "publish_status"],
    )
    op.create_index("idx_promotion_pr", "promotion", ["tenant_id", "pr_id"])
    # 重复检测 + 关联查询
    op.create_index(
        "idx_promotion_blogger",
        "promotion",
        ["tenant_id", "blogger_id", "publish_status"],
    )
    op.create_index(
        "idx_promotion_style",
        "promotion",
        ["tenant_id", "style_id", "publish_status"],
    )
    # 排序 + urge 计算
    op.create_index(
        "idx_promotion_cooperation_date",
        "promotion",
        ["tenant_id", "cooperation_date"],
    )
    op.create_index(
        "idx_promotion_scheduled_date",
        "promotion",
        ["tenant_id", "scheduled_publish_date"],
    )
    op.create_index(
        "idx_promotion_publish_dates",
        "promotion",
        ["tenant_id", "publish_status", "scheduled_publish_date"],
    )
    op.create_index(
        "idx_promotion_settlement_status",
        "promotion",
        ["tenant_id", "settlement_status"],
    )
    op.create_index(
        "idx_promotion_recall_status",
        "promotion",
        ["tenant_id", "recall_status"],
    )

    # GIN trgm 索引（对应 keyword 模糊搜索路径）
    op.execute(
        """
CREATE INDEX idx_promotion_internal_code_trgm ON promotion
USING gin (internal_code gin_trgm_ops) WHERE is_active = true;
""".strip()
    )
    op.execute(
        """
CREATE INDEX idx_promotion_style_code_snapshot_trgm ON promotion
USING gin (style_code_snapshot gin_trgm_ops) WHERE is_active = true;
""".strip()
    )
    op.execute(
        """
CREATE INDEX idx_promotion_short_name_trgm ON promotion
USING gin (style_short_name_snapshot gin_trgm_ops) WHERE is_active = true;
""".strip()
    )

    # ========================================================================
    # 4) promotion_sequence 表
    # ========================================================================
    op.create_table(
        "promotion_sequence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date_key", sa.Date(), nullable=False),
        sa.Column(
            "last_seq", sa.Integer(), nullable=False, server_default=sa.text("0")
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
            ["tenant_id"], ["tenant.id"], ondelete="RESTRICT",
            name="fk_promotion_sequence_tenant",
        ),
        sa.CheckConstraint(
            "last_seq >= 0", name="ck_promotion_sequence_nonneg"
        ),
        sa.CheckConstraint(
            "last_seq <= 9999", name="ck_promotion_sequence_max"
        ),
    )
    op.create_index(
        "uq_promotion_sequence",
        "promotion_sequence",
        ["tenant_id", "date_key"],
        unique=True,
    )

    # ========================================================================
    # 5) RLS 策略
    # ========================================================================
    op.execute(enable_rls_sql("promotion"))
    op.execute(enable_rls_sql("promotion_sequence"))


def downgrade() -> None:
    op.execute(disable_rls_sql("promotion_sequence"))
    op.execute(disable_rls_sql("promotion"))

    op.drop_index("uq_promotion_sequence", table_name="promotion_sequence")
    op.drop_table("promotion_sequence")

    op.execute("DROP INDEX IF EXISTS idx_promotion_short_name_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_promotion_style_code_snapshot_trgm;")
    op.execute("DROP INDEX IF EXISTS idx_promotion_internal_code_trgm;")

    op.drop_index("idx_promotion_recall_status", table_name="promotion")
    op.drop_index("idx_promotion_settlement_status", table_name="promotion")
    op.drop_index("idx_promotion_publish_dates", table_name="promotion")
    op.drop_index("idx_promotion_scheduled_date", table_name="promotion")
    op.drop_index("idx_promotion_cooperation_date", table_name="promotion")
    op.drop_index("idx_promotion_style", table_name="promotion")
    op.drop_index("idx_promotion_blogger", table_name="promotion")
    op.drop_index("idx_promotion_pr", table_name="promotion")
    op.drop_index("idx_promotion_tenant_active", table_name="promotion")
    op.drop_index(
        "uq_promotion_internal_code",
        table_name="promotion",
        postgresql_where=sa.text("is_active = true"),
    )

    op.drop_table("promotion")
    # 不删除 pg_trgm（其他单元仍在用）
