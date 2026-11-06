"""具体账号组合报价和服务端约定快照；历史空快照保持原义。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op
from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision = "075_blogger_account_groups"
down_revision = "074_blogger_audience_captures"
branch_labels = None
depends_on = None


def common_columns():
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "blogger_account_group",
        *common_columns(),
        sa.Column("name", sa.String(128), nullable=False),
    )
    op.create_index(
        "uq_account_group_tenant_id", "blogger_account_group", ["tenant_id", "id"], unique=True
    )
    op.create_table(
        "blogger_account_member",
        *common_columns(),
        sa.Column("group_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("blogger_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["blogger_account_group.tenant_id", "blogger_account_group.id"],
            name="fk_account_member_group",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "blogger_id"],
            ["blogger.tenant_id", "blogger.id"],
            name="fk_account_member_blogger",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "uq_account_member_group",
        "blogger_account_member",
        ["tenant_id", "group_id", "blogger_id"],
        unique=True,
    )
    op.create_index(
        "uq_account_member_active",
        "blogger_account_member",
        ["tenant_id", "blogger_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "blogger_account_quote",
        *common_columns(),
        sa.Column("group_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("account_key", sa.String(369), nullable=False),
        sa.Column("account_ids", pg.JSONB(), nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["blogger_account_group.tenant_id", "blogger_account_group.id"],
            name="fk_account_quote_group",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_account_quote_nonnegative"),
    )
    op.create_index(
        "uq_account_quote_key",
        "blogger_account_quote",
        ["tenant_id", "group_id", "account_key"],
        unique=True,
    )
    for table in ("blogger_account_group", "blogger_account_member", "blogger_account_quote"):
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
        op.execute(enable_rls_sql(table))
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO clothing_app")
    for table in ("negotiation", "promotion"):
        op.add_column(table, sa.Column("account_snapshot", pg.JSONB(), nullable=True))
    op.add_column(
        "promotion_platform_post", sa.Column("account_id", pg.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("promotion_platform_post", sa.Column("account", sa.String(128), nullable=True))
    op.add_column("promotion_platform_post", sa.Column("nickname", sa.String(128), nullable=True))
    op.add_column(
        "promotion_platform_post",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_foreign_key(
        "fk_post_account_tenant",
        "promotion_platform_post",
        "blogger",
        ["tenant_id", "account_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.drop_index("uq_platform_post", table_name="promotion_platform_post")
    op.create_index(
        "uq_platform_post",
        "promotion_platform_post",
        ["tenant_id", "promotion_id", "platform"],
        unique=True,
        postgresql_where=sa.text("account_id IS NULL"),
    )
    op.create_index(
        "uq_platform_post_account",
        "promotion_platform_post",
        ["tenant_id", "promotion_id", "account_id"],
        unique=True,
        postgresql_where=sa.text("account_id IS NOT NULL"),
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM negotiation WHERE account_snapshot IS NOT NULL) OR EXISTS (SELECT 1 FROM promotion WHERE account_snapshot IS NOT NULL) OR EXISTS (SELECT 1 FROM promotion_platform_post WHERE account_id IS NOT NULL)"
            )
        )
        .scalar()
    ):
        raise RuntimeError("已有账号约定或履约记录，不能无损降级；请先按发布回退方案处理")
    op.drop_index("uq_platform_post_account", table_name="promotion_platform_post")
    op.drop_index("uq_platform_post", table_name="promotion_platform_post")
    op.create_index(
        "uq_platform_post",
        "promotion_platform_post",
        ["tenant_id", "promotion_id", "platform"],
        unique=True,
    )
    op.drop_constraint("fk_post_account_tenant", "promotion_platform_post", type_="foreignkey")
    for column in ("is_primary", "nickname", "account", "account_id"):
        op.drop_column("promotion_platform_post", column)
    for table in ("promotion", "negotiation"):
        op.drop_column(table, "account_snapshot")
    for table in ("blogger_account_quote", "blogger_account_member", "blogger_account_group"):
        op.execute(disable_rls_sql(table))
        op.drop_table(table)
