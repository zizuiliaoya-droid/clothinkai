"""U05 - 创建 shared attachment 基础设施 + 财务结款核心表（永久 UNIQUE，FB3）

Revision ID: 007_u05_create_settlement_tables
Revises: 006_u04_create_promotion_tables
Create Date: 2026-05-26

两段结构（详见 U05 code-generation-plan §1.0 — Option A 修订）：

【上半段】shared attachment 基础设施补齐（不是 U05 私有表）：
- attachment 表（11 字段）+ 3 索引（含永久 UNIQUE uq_attachment_r2_key）+ 3 CHECK
- 1 RLS 策略（tenant_isolation）

【下半段】U05 财务结款核心表：
- 3 张表（settlement / settlement_extra_item / settlement_sequence）
- 11 settlement 索引（含 3 永久 UNIQUE，无 partial WHERE，FB3）
- 1 GIN trgm 索引（settlement_no，无 partial WHERE，FB3）
- 2 RLS 策略（settlement / settlement_extra_item；settlement_sequence 不需要）
- settlement.payment_proof_attachment_id FK → attachment.id（FB4，不裸存 R2 key）

不修改 pg_trgm 扩展（U02 migration 004 已启用）。
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "007_u05_create_settlement_tables"
down_revision: str | Sequence[str] | None = "006_u04_create_promotion_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _upgrade_attachment()
    _upgrade_settlement()


def downgrade() -> None:
    _downgrade_settlement()
    _downgrade_attachment()


# ===========================================================================
# 上半段：shared attachment 基础设施（U05 触发补齐 — 不是 U05 私有表）
# ===========================================================================


def _upgrade_attachment() -> None:
    op.create_table(
        "attachment",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bucket", sa.String(16), nullable=False),
        sa.Column("r2_key", sa.String(512), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(255), nullable=True),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'uploading'"),
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            name="fk_attachment_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["user.id"], ondelete="SET NULL",
            name="fk_attachment_created_by",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_attachment_size_nonneg"),
        sa.CheckConstraint(
            "bucket IN ('public', 'private', 'credentials', 'backups')",
            name="ck_attachment_bucket",
        ),
        sa.CheckConstraint(
            "status IN ('uploading', 'ready')", name="ck_attachment_status"
        ),
    )
    op.create_index(
        "idx_attachment_tenant_purpose", "attachment", ["tenant_id", "purpose"]
    )
    op.create_index(
        "idx_attachment_status", "attachment", ["status", "created_at"]
    )  # V1 GC 任务用
    # 永久 UNIQUE（R2 path 全局唯一）
    op.create_index("uq_attachment_r2_key", "attachment", ["r2_key"], unique=True)

    # RLS：attachment 跨租户访问由 application layer（ProofAttachmentValidator）+ RLS 双重防护
    op.execute(enable_rls_sql("attachment"))


def _downgrade_attachment() -> None:
    op.execute(disable_rls_sql("attachment"))
    op.drop_index("uq_attachment_r2_key", table_name="attachment")
    op.drop_index("idx_attachment_status", table_name="attachment")
    op.drop_index("idx_attachment_tenant_purpose", table_name="attachment")
    op.drop_table("attachment")


# ===========================================================================
# 下半段：U05 财务结款核心表（FB3 永久 UNIQUE，无 is_active）
# ===========================================================================


def _upgrade_settlement() -> None:
    # 1) settlement 表（注意：无 is_active 字段，FB3）
    op.create_table(
        "settlement",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("style_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("paid_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "payment_proof_attachment_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("settlement_no", sa.String(64), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("payment_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("note_title", sa.String(255), nullable=True),
        sa.Column("remark", sa.Text(), nullable=True),
        sa.Column(
            "settlement_status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'待核查'"),  # FB1 起点
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_action", sa.String(16), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column(
            "request_event_id", postgresql.UUID(as_uuid=True), nullable=False
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
        # FK
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_settlement_tenant"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT", name="fk_settlement_promotion"),
        sa.ForeignKeyConstraint(["blogger_id"], ["blogger.id"], ondelete="RESTRICT", name="fk_settlement_blogger"),
        sa.ForeignKeyConstraint(["style_id"], ["style.id"], ondelete="RESTRICT", name="fk_settlement_style"),
        sa.ForeignKeyConstraint(["pr_id"], ["user.id"], ondelete="SET NULL", name="fk_settlement_pr"),
        sa.ForeignKeyConstraint(["reviewed_by"], ["user.id"], ondelete="SET NULL", name="fk_settlement_reviewer"),
        sa.ForeignKeyConstraint(["paid_by"], ["user.id"], ondelete="SET NULL", name="fk_settlement_paid_by"),
        sa.ForeignKeyConstraint(
            ["payment_proof_attachment_id"], ["attachment.id"],
            ondelete="RESTRICT", name="fk_settlement_payment_proof",
        ),
        # CHECK
        sa.CheckConstraint("amount >= 0", name="ck_settlement_amount_nonneg"),
        sa.CheckConstraint("total_amount >= 0", name="ck_settlement_total_amount_nonneg"),
        sa.CheckConstraint(
            "payment_amount IS NULL OR payment_amount >= 0",
            name="ck_settlement_payment_amount_nonneg",
        ),
    )

    # 2) settlement 索引（10 个 b-tree，含 3 永久 UNIQUE — 无 partial WHERE，FB3）
    op.create_index("uq_settlement_no", "settlement", ["tenant_id", "settlement_no"], unique=True)
    op.create_index("uq_settlement_promotion", "settlement", ["tenant_id", "promotion_id"], unique=True)
    op.create_index("uq_settlement_request_event_id", "settlement", ["request_event_id"], unique=True)
    op.create_index(
        "idx_settlement_tenant_status", "settlement",
        ["tenant_id", "settlement_status", sa.text("created_at DESC")],
    )
    op.create_index("idx_settlement_blogger", "settlement", ["tenant_id", "blogger_id"])
    op.create_index("idx_settlement_style", "settlement", ["tenant_id", "style_id"])
    op.create_index("idx_settlement_pr", "settlement", ["tenant_id", "pr_id"])
    op.create_index("idx_settlement_payment_date", "settlement", ["tenant_id", "payment_date"])
    op.create_index("idx_settlement_reviewed_by", "settlement", ["tenant_id", "reviewed_by"])
    op.create_index("idx_settlement_paid_by", "settlement", ["tenant_id", "paid_by"])

    # GIN trgm（settlement_no 关键字搜索；无 partial WHERE，FB3：所有 settlement 都活跃）
    op.execute(
        "CREATE INDEX idx_settlement_no_trgm ON settlement "
        "USING gin (settlement_no gin_trgm_ops);"
    )

    # 3) settlement_extra_item 表
    op.create_table(
        "settlement_extra_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("settlement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_type", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_extra_item_tenant"),
        sa.ForeignKeyConstraint(["settlement_id"], ["settlement.id"], ondelete="CASCADE", name="fk_extra_item_settlement"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL", name="fk_extra_item_created_by"),
        sa.CheckConstraint("amount > 0", name="ck_extra_item_amount_pos"),
    )
    op.create_index(
        "idx_extra_item_settlement", "settlement_extra_item",
        ["tenant_id", "settlement_id"],
    )

    # 4) settlement_sequence 表（复用 U04 FB2 模式）
    op.create_table(
        "settlement_sequence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("date_key", sa.Date(), nullable=False),
        sa.Column("last_seq", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT", name="fk_settlement_sequence_tenant"),
        sa.CheckConstraint("last_seq >= 0", name="ck_settlement_sequence_nonneg"),
        sa.CheckConstraint("last_seq <= 9999", name="ck_settlement_sequence_max"),
    )
    op.create_index(
        "uq_settlement_sequence", "settlement_sequence",
        ["tenant_id", "date_key"], unique=True,
    )

    # 5) RLS 策略（settlement_sequence 不启用 RLS）
    op.execute(enable_rls_sql("settlement"))
    op.execute(enable_rls_sql("settlement_extra_item"))


def _downgrade_settlement() -> None:
    op.execute(disable_rls_sql("settlement_extra_item"))
    op.execute(disable_rls_sql("settlement"))

    op.drop_index("uq_settlement_sequence", table_name="settlement_sequence")
    op.drop_table("settlement_sequence")

    op.drop_index("idx_extra_item_settlement", table_name="settlement_extra_item")
    op.drop_table("settlement_extra_item")

    op.execute("DROP INDEX IF EXISTS idx_settlement_no_trgm;")
    op.drop_index("idx_settlement_paid_by", table_name="settlement")
    op.drop_index("idx_settlement_reviewed_by", table_name="settlement")
    op.drop_index("idx_settlement_payment_date", table_name="settlement")
    op.drop_index("idx_settlement_pr", table_name="settlement")
    op.drop_index("idx_settlement_style", table_name="settlement")
    op.drop_index("idx_settlement_blogger", table_name="settlement")
    op.drop_index("idx_settlement_tenant_status", table_name="settlement")
    op.drop_index("uq_settlement_request_event_id", table_name="settlement")
    op.drop_index("uq_settlement_promotion", table_name="settlement")
    op.drop_index("uq_settlement_no", table_name="settlement")
    op.drop_table("settlement")
    # 不删除 pg_trgm 扩展（U02 / U04 仍在使用）
