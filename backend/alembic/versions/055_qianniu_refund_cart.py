"""千牛日报补 typed 列：成功退款金额、商品加购件数（修复报表里退款 / 加购恒为 0）。

## 为什么

报表一直从 ``qianniu_daily.extra->>'refund_amount'`` / ``extra->>'add_cart_count'`` 取
退款额与加购数，但导入从来没写过这两个英文键 —— ``extra`` 里存的是生意参谋导出的
**原始中文表头**（「成功退款金额」「商品加购件数」……），数值还带千分位（"5,652.00"）。
于是所有报表的退款与加购都是 0：净投产比等于退货前投产比、店铺实际销售额没扣退款、
加购成本为空。生产 310 行日报实际成功退款 ¥95,955.20，是支付额 ¥171,232.40 的 56%。

测试没拦住，是因为测试数据也按代码的假设往 ``extra`` 里塞英文键 —— 测试和代码犯的是
同一个错，两边互相印证，谁也没碰过一份真实导出。

## 做法

- 加两个 typed 列，导入时 adapter 按中文表头解析（去千分位；空串、"-" 记 NULL）
- 存量行从 ``extra`` 回填，规则与 adapter 一致；认不出的值留 NULL，不编造 0
- 报表改读 typed 列。``extra`` 原样保留，「千牛其余几十列」的展示照旧从它来

## 清空覆盖记录

``product_roi_summary`` 的退款 / 加购按旧口径存的全是 0。按 054 定下的规则 —— 口径变了，
覆盖记录就作废：清空后读取回退实时（立即正确），下一次每小时刷新把最近 31 天重写一遍。

Revision ID: 055_qianniu_refund_cart
Revises: 054_summary_read_cols
Create Date: 2026-10-02
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "055_qianniu_refund_cart"
down_revision: str | Sequence[str] | None = "054_summary_read_cols"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 与 app/modules/importer/adapters/qianniu.py 的 _to_optional_decimal / _to_optional_int
# 同一规则：去空白与千分位后必须是纯数字，否则 NULL。写成 CASE 而不是直接 CAST ——
# 一个脏值（"-"、"1.2万"）就会让整条 UPDATE 失败、migration 回滚。
_BACKFILL_SQL = """
UPDATE qianniu_daily
SET refund_amount = CASE
      WHEN replace(btrim(extra->>'成功退款金额'), ',', '') ~ '^-?[0-9]+([.][0-9]+)?$'
      THEN CAST(replace(btrim(extra->>'成功退款金额'), ',', '') AS numeric(12, 2))
    END,
    add_cart_count = CASE
      WHEN replace(btrim(extra->>'商品加购件数'), ',', '') ~ '^-?[0-9]+$'
      THEN CAST(replace(btrim(extra->>'商品加购件数'), ',', '') AS integer)
    END
WHERE extra IS NOT NULL
"""


def upgrade() -> None:
    op.add_column("qianniu_daily", sa.Column("refund_amount", sa.Numeric(12, 2), nullable=True))
    op.add_column("qianniu_daily", sa.Column("add_cart_count", sa.Integer(), nullable=True))
    op.execute(_BACKFILL_SQL)
    # 见模块 docstring「清空覆盖记录」
    op.execute("DELETE FROM report_summary_coverage")
    print("[055] 千牛日报补退款 / 加购 typed 列并从 extra 回填；覆盖记录已清空，下一次刷新前报表回退实时")


def downgrade() -> None:
    # 回到 054 后报表又按旧口径读，按新口径写下的汇总同样作废
    op.execute("DELETE FROM report_summary_coverage")
    op.drop_column("qianniu_daily", "add_cart_count")
    op.drop_column("qianniu_daily", "refund_amount")
