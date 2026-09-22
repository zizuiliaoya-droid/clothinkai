"""仓库打单筛选用的部分索引（source_extra 打单地址 / 发货单号）。

背景：仓库打单页原先拉一页推广（page_size=100）后在浏览器里过滤「打单地址非空」，
既慢（要带上 CTE 的催单状态与 dual_platform EXISTS 计算整页数据）又漏
（第 101 条以后的打单单永远看不到）。改为服务端筛选后，这里补上匹配的部分索引。

只有极少量推广会填打单地址，用部分索引而不是全表表达式索引：索引体积极小，
且能同时支撑列表的 ORDER BY cooperation_date DESC, created_at DESC。

索引谓词必须与 ``PromotionRepository.list_with_cte`` 里生成的表达式逐字一致
（``COALESCE(BTRIM(source_extra->>'打单地址'), '') <> ''``），否则 planner 不会命中。

Revision ID: 032_promo_warehouse_idx
Revises: 031_promo_payment_qr
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "032_promo_warehouse_idx"
down_revision: str | Sequence[str] | None = "031_promo_payment_qr"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRINT_ADDRESS_PREDICATE = "COALESCE(BTRIM(source_extra->>'打单地址'), '') <> ''"
_WAYBILL_PREDICATE = "COALESCE(BTRIM(source_extra->>'发货单号'), '') <> ''"


def upgrade() -> None:
    op.create_index(
        "idx_promotion_print_address",
        "promotion",
        ["tenant_id", sa.text("cooperation_date DESC"), sa.text("created_at DESC")],
        postgresql_where=sa.text(_PRINT_ADDRESS_PREDICATE),
    )
    op.create_index(
        "idx_promotion_waybill",
        "promotion",
        ["tenant_id", sa.text("cooperation_date DESC"), sa.text("created_at DESC")],
        postgresql_where=sa.text(_WAYBILL_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index("idx_promotion_waybill", table_name="promotion")
    op.drop_index("idx_promotion_print_address", table_name="promotion")
