"""局部画像采集记录。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision = "074_blogger_audience_captures"
down_revision = "073_style_optional_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "blogger_audience_capture",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("blogger_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("dimension", sa.String(16), nullable=False),
        sa.Column("collected_on", sa.Date(), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("completeness", sa.String(16), nullable=False),
        sa.Column("estimated", sa.Boolean(), nullable=False),
        sa.Column("tgi_source", sa.String(256)),
        sa.Column("entries", pg.JSONB(), nullable=False),
        sa.Column(
            "import_batch_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("import_batch.id", ondelete="RESTRICT"),
        ),
        sa.Column("row_numbers", pg.JSONB(), nullable=False),
        sa.Column("created_by", pg.UUID(as_uuid=True)),
        sa.ForeignKeyConstraint(
            ["tenant_id", "blogger_id"],
            ["blogger.tenant_id", "blogger.id"],
            name="fk_audience_blogger_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["user.tenant_id", "user.id"],
            name="fk_audience_actor_tenant",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "uq_blogger_audience_capture",
        "blogger_audience_capture",
        ["tenant_id", "blogger_id", "dimension", "collected_on"],
        unique=True,
    )
    op.create_index(
        "ix_blogger_audience_capture_tenant_id", "blogger_audience_capture", ["tenant_id"]
    )
    op.execute(enable_rls_sql("blogger_audience_capture"))
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON blogger_audience_capture TO clothing_app")


def downgrade() -> None:
    # 去掉本功能的当前引用，保留旧画像与note_stats；降级后不留下失效记录ID。
    op.execute(
        "UPDATE blogger SET audience_profile = audience_profile - 'structured_v1' WHERE audience_profile ? 'structured_v1'"
    )
    op.execute(disable_rls_sql("blogger_audience_capture"))
    op.drop_table("blogger_audience_capture")
