"""Allow unselected style and unknown fee on negotiation drafts only."""

import sqlalchemy as sa

from alembic import op

revision = "073_style_optional_drafts"
down_revision = "072_blogger_work_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("negotiation", "style_id", nullable=True)
    op.alter_column("negotiation", "quote_amount", nullable=True, server_default=None)
    op.create_check_constraint(
        "ck_negotiation_submitted_complete",
        "negotiation",
        "status = '草稿' OR (style_id IS NOT NULL AND quote_amount IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_negotiation_goods_requires_style",
        "negotiation",
        "style_id IS NOT NULL OR goods_main_id IS NULL",
    )


def downgrade() -> None:
    incomplete = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM negotiation WHERE style_id IS NULL OR quote_amount IS NULL)"
            )
        )
        .scalar_one()
    )
    if incomplete:
        raise RuntimeError("存在未补齐款式或服务费的草稿，请先补齐后再降级；不会自动填款或填0")
    op.drop_constraint("ck_negotiation_goods_requires_style", "negotiation", type_="check")
    op.drop_constraint("ck_negotiation_submitted_complete", "negotiation", type_="check")
    op.alter_column("negotiation", "quote_amount", nullable=False, server_default=sa.text("0"))
    op.alter_column("negotiation", "style_id", nullable=False)
