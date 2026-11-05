"""批量创建行键；旧谈款不回填，不新增批次状态。"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

from alembic import op

revision = "076_negotiation_creation_keys"
down_revision = "075_blogger_account_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("client_create_key", "creation_group_id", "creation_pr_id"):
        op.add_column("negotiation", sa.Column(name, pg.UUID(as_uuid=True), nullable=True))
    op.add_column("negotiation", sa.Column("creation_request_hash", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_negotiation_creation_pr",
        "negotiation",
        "user",
        ["tenant_id", "creation_pr_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_negotiation_creation_identity",
        "negotiation",
        "(client_create_key IS NULL AND creation_group_id IS NULL AND creation_pr_id IS NULL AND creation_request_hash IS NULL) OR (client_create_key IS NOT NULL AND creation_group_id IS NOT NULL AND creation_pr_id IS NOT NULL AND creation_request_hash IS NOT NULL)",
    )
    op.create_index(
        "uq_negotiation_create_key",
        "negotiation",
        ["tenant_id", "creation_pr_id", "client_create_key"],
        unique=True,
        postgresql_where=sa.text("client_create_key IS NOT NULL"),
    )
    op.create_index(
        "idx_negotiation_creation_group",
        "negotiation",
        ["tenant_id", "creation_group_id"],
        postgresql_where=sa.text("creation_group_id IS NOT NULL"),
    )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM negotiation WHERE client_create_key IS NOT NULL)")
    ):
        raise RuntimeError("已有批量创建行键，不能丢弃防重复记录；请按发布回退方案处理")
    op.drop_index("idx_negotiation_creation_group", table_name="negotiation")
    op.drop_index("uq_negotiation_create_key", table_name="negotiation")
    op.drop_constraint("ck_negotiation_creation_identity", "negotiation", type_="check")
    op.drop_constraint("fk_negotiation_creation_pr", "negotiation", type_="foreignkey")
    for name in (
        "creation_request_hash",
        "creation_pr_id",
        "creation_group_id",
        "client_create_key",
    ):
        op.drop_column("negotiation", name)
