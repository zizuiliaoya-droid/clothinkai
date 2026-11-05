"""非独占博主认领、工作微信及租户显式启用。"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "070_blogger_claims"
down_revision: str | Sequence[str] | None = "069_remove_unused_shop_caches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("blogger", "user", "dict_item"):
        op.create_index(f"uq_{table}_tenant_id", table, ["tenant_id", "id"], unique=True)
    op.add_column("tenant", sa.Column("blogger_claims_enabled_at", sa.DateTime(timezone=True)))
    op.add_column("import_batch", sa.Column("claim_work_wechat_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_import_claim_work_wechat",
        "import_batch",
        "dict_item",
        ["claim_work_wechat_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "blogger_claim",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("blogger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("operated_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_wechat_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["blogger_id"], ["blogger.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pr_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operated_by"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["work_wechat_id"], ["dict_item.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "uq_blogger_claim", "blogger_claim", ["tenant_id", "blogger_id", "pr_id"], unique=True
    )
    op.create_index("ix_blogger_claim_pr", "blogger_claim", ["tenant_id", "pr_id", "blogger_id"])
    for col, table, name in (
        ("blogger_id", "blogger", "blogger"),
        ("pr_id", "user", "pr"),
        ("operated_by", "user", "actor"),
        ("work_wechat_id", "dict_item", "work"),
    ):
        op.create_foreign_key(
            f"fk_claim_{name}_tenant",
            "blogger_claim",
            table,
            ["tenant_id", col],
            ["tenant_id", "id"],
        )
    op.execute(enable_rls_sql("blogger_claim"))
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON blogger_claim TO clothing_app")


def downgrade() -> None:
    op.execute(disable_rls_sql("blogger_claim"))
    op.drop_table("blogger_claim")
    op.drop_constraint("fk_import_claim_work_wechat", "import_batch", type_="foreignkey")
    op.drop_column("import_batch", "claim_work_wechat_id")
    op.drop_column("tenant", "blogger_claims_enabled_at")
    for table in ("blogger", "user", "dict_item"):
        op.drop_index(f"uq_{table}_tenant_id", table_name=table)
