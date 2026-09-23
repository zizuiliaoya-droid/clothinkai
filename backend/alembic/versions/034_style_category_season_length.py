"""放宽 style.category / style.season 长度，与 dict_item.value 对齐。

类目与季节自 migration 027 起改为租户自维护字典（``dict_item.value`` 是 varchar(64)），
但 ``style`` 表这两列仍是建表时的 varchar(32) / varchar(16)，接口校验又写着 max_length=32。
结果是字典里能建、下拉里能选的值，保存时却被拒 —— season 超过 16 个字符甚至直接写库报错。

三处统一到 64：字典能录入的值就应该能存进款式。
PostgreSQL 放宽 varchar 长度上限只改 catalog，不重写表。

Revision ID: 034_style_cat_season_len
Revises: 033_order_payment_qr
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "034_style_cat_season_len"
down_revision: str | Sequence[str] | None = "033_order_payment_qr"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "style",
        "category",
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=False,
    )
    op.alter_column(
        "style",
        "season",
        existing_type=sa.String(16),
        type_=sa.String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    # 回退会截断超长值，故先确认没有超出原长度的数据再收窄。
    bind = op.get_bind()
    too_long = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM style "
            "WHERE LENGTH(category) > 32 OR (season IS NOT NULL AND LENGTH(season) > 16)"
        )
    ).scalar_one()
    if too_long:
        raise RuntimeError(
            f"有 {too_long} 行 category/season 超过回退后的长度上限，"
            "请先修正这些值再执行 downgrade（否则数据会被截断）。"
        )
    op.alter_column(
        "style",
        "season",
        existing_type=sa.String(64),
        type_=sa.String(16),
        existing_nullable=True,
    )
    op.alter_column(
        "style",
        "category",
        existing_type=sa.String(64),
        type_=sa.String(32),
        existing_nullable=False,
    )
