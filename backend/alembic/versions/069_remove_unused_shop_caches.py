"""移除无人读取的店铺周/月缓存；降级恢复空表，旧刷新代码可重建内容。"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.security.rls import enable_rls_sql

revision: str = "069_remove_unused_shop_caches"
down_revision: str | Sequence[str] | None = "068_promotion_content_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (("shop_week_summary", "week_start"), ("shop_month_summary", "period_month"))


def upgrade() -> None:
    # 无CASCADE：意外的外部依赖应让迁移失败，不能扩大删除范围。
    for table, _ in _TABLES:
        op.drop_table(table)


def downgrade() -> None:
    # 逐列保留052+062的最终schema及顺序，不依赖已删除的当前ORM模型。
    for table, bucket in _TABLES:
        op.create_table(
            table,
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
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
            sa.Column(bucket, sa.Date(), nullable=False),
            sa.Column("visitors", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("pay_amount", sa.Numeric(16, 2), nullable=False, server_default=sa.text("0")),
            sa.Column("pay_orders", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("refund_amount", sa.Numeric(), nullable=False, server_default=sa.text("0")),
            *(
                sa.Column(name, sa.Numeric(16, 2), nullable=False, server_default=sa.text("0"))
                for name in (
                    "brushing_amount",
                    "ad_spend",
                    "promo_cost",
                    "live_pay_amount",
                    "live_ad_spend",
                )
            ),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        )
        op.create_index(f"uq_{table}", table, ["tenant_id", bucket], unique=True)
        op.execute(enable_rls_sql(table))
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO clothing_app")
