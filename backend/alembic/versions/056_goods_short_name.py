"""商品 / 套装加「商品简称」（goods_main.short_name）。

## 为什么

商品名称是从款式名带过来的店铺标题，平均 21 个字、最长 36 个字（「LENNEA芭蕾风假两件
蕾丝花边拼接阔腿裤女设计感高腰直筒休闲裤」），列表、报表、下拉里都显示不全。业务要一个
短名字用于展示。

数据里没有现成的简称可以回填：款式上的 ``short_name`` 264 个全空，千牛导出也没有简称列。
所以新列可空、不回填，由业务在商品页逐个填写；没填的地方继续显示全称。

## 不清空汇总覆盖记录

报表的商品名称是读取时从 ``goods_main`` 实时 JOIN 的（``GOODS_META_COLUMNS``），
汇总表里不存名称，加列不改变任何已汇总的数字。

Revision ID: 056_goods_short_name
Revises: 055_qianniu_refund_cart
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "056_goods_short_name"
down_revision: str | Sequence[str] | None = "055_qianniu_refund_cart"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("goods_main", sa.Column("short_name", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("goods_main", "short_name")
