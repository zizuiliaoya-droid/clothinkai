"""博主作品采集快照，保留每次采集及导入来源。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision = "072_blogger_work_snapshots"
down_revision = "071_line_comment_reads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blogger_work_snapshot",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("blogger_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("work_key", sa.String(68), nullable=False),
        sa.Column("work_id", sa.String(128)),
        sa.Column("work_url", sa.String(2048)),
        sa.Column("title", sa.String(500)),
        sa.Column("published_on", sa.Date(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        *(
            sa.Column(f"{metric}_count", sa.BigInteger())
            for metric in ("like", "comment", "share", "collect", "play")
        ),
        sa.Column(
            "import_batch_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("import_batch.id", ondelete="RESTRICT"),
        ),
        sa.Column("row_number", sa.Integer()),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "blogger_id"],
            ["blogger.tenant_id", "blogger.id"],
            name="fk_work_blogger_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["user.tenant_id", "user.id"],
            name="fk_work_actor_tenant",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "uq_blogger_work_snapshot",
        "blogger_work_snapshot",
        ["tenant_id", "blogger_id", "work_key", "collected_at"],
        unique=True,
    )
    op.create_index("ix_blogger_work_snapshot_tenant_id", "blogger_work_snapshot", ["tenant_id"])
    op.execute(enable_rls_sql("blogger_work_snapshot"))
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON blogger_work_snapshot TO clothing_app")


def downgrade() -> None:
    op.execute(disable_rls_sql("blogger_work_snapshot"))
    op.drop_table("blogger_work_snapshot")
