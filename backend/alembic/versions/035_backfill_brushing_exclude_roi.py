"""回填刷单的 exclude_from_roi 标记，让投产报表的「剔除刷单」真正生效。

投产报表按 ``exclude_from_roi = true`` 把刷单金额从支付金额里减掉。但导入适配器
此前对「是否剔除ROI」列用 ``bool(parsed.get(...))``，Excel 没填这一列时得到 False，
于是所有从 Excel 导入的刷单标记都是 false —— 子查询一条都匹配不到，
剔除功能形同虚设，刷单金额全额计入销售额，投产比虚高。

适配器已改为按单据类型取默认值（刷单 true / 拍单 false）。这里回填历史数据：
只动 ``order_type='刷单'`` 且标记为 false 的行，拍单不碰（拍单是真实店铺下单，
本就该计入销售额）。

注意：回填后投产比会下降，那是修正后的真实数字。

Revision ID: 035_backfill_brush_roi
Revises: 034_style_cat_season_len
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "035_backfill_brush_roi"
down_revision: str | Sequence[str] | None = "034_style_cat_season_len"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "UPDATE order_adjustment SET exclude_from_roi = true, updated_at = NOW() "
            "WHERE order_type = '刷单' AND exclude_from_roi = false"
        )
    )
    print(f"[035] 回填刷单 exclude_from_roi=true：{result.rowcount} 行")


def downgrade() -> None:
    # 无法区分「回填出来的 true」和「本来就是 true 的」，所以不做反向数据变更。
    # 真要回退请连同适配器改动一起回退，并按业务判断逐条修正。
    pass
