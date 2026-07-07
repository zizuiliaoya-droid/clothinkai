"""可维护字典 dict_item（类目/季节等由租户自定义），并放开 style.category/season 枚举约束。

- 建 dict_item 表 + RLS（tenant_isolation）
- 为已有租户 seed 默认 类目/季节 值（含 "未分类"，兼容导入历史数据）

Revision ID: 027_dict_item
Revises: 026_style_qianniu
Create Date: 2026-07-06
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "027_dict_item"
down_revision: str | Sequence[str] | None = "026_style_qianniu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dict_item",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenant.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("dict_type", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
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
    )
    op.create_index(
        "uq_dict_item", "dict_item", ["tenant_id", "dict_type", "value"], unique=True
    )
    op.create_index(
        "idx_dict_item_type", "dict_item", ["tenant_id", "dict_type", "is_active"]
    )

    # RLS
    op.execute('ALTER TABLE "dict_item" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "dict_item" FORCE ROW LEVEL SECURITY')
    op.execute(
        """
CREATE POLICY tenant_isolation ON "dict_item"
    FOR ALL
    TO clothing_app
    USING (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    )
    WITH CHECK (
        tenant_id = current_setting('app.tenant_id', true)::uuid
        OR current_setting('app.bypass_rls', true) = 'on'
    )
"""
    )

    # 为已有租户 seed 默认字典值
    op.execute(
        """
INSERT INTO dict_item (id, tenant_id, dict_type, value, sort_order, is_active, created_at, updated_at)
SELECT gen_random_uuid(), t.id, d.dict_type, d.value, d.ord, true, now(), now()
FROM tenant t
CROSS JOIN (
    VALUES
        ('category','连衣裙',1),('category','上衣',2),('category','裤装',3),
        ('category','裙装',4),('category','外套',5),('category','套装',6),
        ('category','配饰',7),('category','未分类',99),
        ('season','春',1),('season','夏',2),('season','秋',3),
        ('season','冬',4),('season','四季',5)
) AS d(dict_type, value, ord)
ON CONFLICT (tenant_id, dict_type, value) DO NOTHING
"""
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS tenant_isolation ON "dict_item"')
    op.drop_index("idx_dict_item_type", table_name="dict_item")
    op.drop_index("uq_dict_item", table_name="dict_item")
    op.drop_table("dict_item")
