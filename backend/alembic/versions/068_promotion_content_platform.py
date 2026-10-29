"""PR9 内容分类快照与仅记录的其他平台；降级会丢弃本批新数据。"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "068_promotion_content_platform"
down_revision: str | Sequence[str] | None = "067_retro_completion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("promotion", sa.Column("content_category", sa.String(8), nullable=True))
    op.execute(
        "UPDATE promotion p SET content_category=b.content_category FROM blogger b "
        "WHERE b.id=p.blogger_id AND b.tenant_id=p.tenant_id "
        "AND b.content_category IS NOT NULL"
    )
    op.create_table(
        "promotion_platform_post",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("promotion_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("platform", sa.String(16), nullable=False),
        sa.Column("publish_url", sa.String(512), nullable=True),
        sa.Column("actual_publish_date", sa.Date(), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["promotion_id"], ["promotion.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["user.id"], ondelete="SET NULL"),
        sa.CheckConstraint("like_count >= 0", name="ck_platform_post_like_nonnegative"),
    )
    op.create_index(
        "uq_platform_post",
        "promotion_platform_post",
        ["tenant_id", "promotion_id", "platform"],
        unique=True,
    )
    op.execute(enable_rls_sql("promotion_platform_post"))
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON promotion_platform_post TO clothing_app")


def downgrade() -> None:
    op.execute(disable_rls_sql("promotion_platform_post"))
    op.drop_table("promotion_platform_post")
    op.drop_column("promotion", "content_category")
