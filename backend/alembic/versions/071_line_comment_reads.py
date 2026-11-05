"""1105 本人留言游标与站内通知关联；071 接认领迁移070。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op
from app.core.security.rls import enable_rls_sql

revision = "071_line_comment_reads"
down_revision = "070_blogger_claims"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "line_comment_read",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenant.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_read_comment_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_line_comment_read_tenant_id", "line_comment_read", ["tenant_id"])
    op.create_index("uq_line_comment_read", "line_comment_read", ["tenant_id", "user_id", "line_id"], unique=True)
    op.execute(enable_rls_sql("line_comment_read"))
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON line_comment_read TO clothing_app")
    op.add_column("notification", sa.Column("line_id", postgresql.UUID(as_uuid=True)))
    op.add_column("notification", sa.Column("event_seq", sa.BigInteger()))
    op.add_column("notification", sa.Column("summary_date", sa.Date()))
    op.create_index("uq_notification_daily_summary", "notification",
                    ["tenant_id", "user_id", "type", "summary_date"], unique=True,
                    postgresql_where=sa.text("summary_date IS NOT NULL"))
    op.create_index("idx_notification_line_comment", "notification",
                    ["tenant_id", "user_id", "line_id", "event_seq"],
                    postgresql_where=sa.text("line_id IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("idx_notification_line_comment", table_name="notification")
    op.drop_index("uq_notification_daily_summary", table_name="notification")
    for column in ("summary_date", "event_seq", "line_id"):
        op.drop_column("notification", column)
    op.drop_table("line_comment_read")
