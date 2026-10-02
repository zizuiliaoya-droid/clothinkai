"""汇总表补列：投产加购数 + 工作进度 4 个催发派生计数（切读取的前置）。

报表读取切到汇总表之前，汇总表必须能独立产出页面上的每一列。逐列核对后缺两组：

1. ``product_roi_summary.add_cart_count`` —— 投产报表「总加购数」「加购成本」两列靠它，
   此前只有实时 SQL 从 ``qianniu_daily.extra`` 抠出来。
2. ``pr_work_progress_summary`` 的档期内 / 催发 / 重要催发 / 超时 4 个计数。

另外改两处 052 的类型为不限精度的 numeric：

- ``pr_work_progress_summary.like_count``（原 bigint）：点赞汇总走 ``like_sum_expr``，
  抖音/快手按 ×0.1 折算，单日结果可以是 1.5 这样的小数。
- ``product_roi_summary.refund_amount``（原 numeric(16,2)）：退款额是从导入的 JSONB 里
  抠出来的，位数没有保证。

两者存成定长都会**逐日**舍入，读取时再把各天加起来 —— 「先舍入再求和」与实时路径的
「先求和再取整」对不上（两天各 1.5 个折算点赞：实时 3，汇总 2+2=4）。存精确值，读取侧
求和后再按实时路径同样的方式取整，两边才一致。其余金额列来自 numeric(12,2) 源列，
逐日求和本来就是精确的，不用改。

## 第 2 组推翻了 052 的一个决定

052 不存这 4 个，理由是「它们依赖 today，固化到历史日期上永远是错的」。复核后这个
理由只对一半：

- 最近 31 天每小时用**刷新时刻的 today** 重算 —— 存下来的是「截至上次刷新」的状态，
  和其他计数一样最多晚一小时，同一行内口径一致；
- 31 天外的日子随历史冻结 —— 这正是 PRD「历史数据只能页面手动刷新」的口径，与
  推广费、销售额等所有历史数字是同一类陈旧，不是新问题。

反过来，不存的话工作进度页只能「10 列读汇总 + 4 列实时」拼起来：同一行里发布数是
一小时前的、超时数是此刻的 —— PR 刚发布一单，超时数立刻减 1 而发布数不动，完成率
与超时率对不上。用户选方案 2 时已接受「发布后工作进度不会马上更新」，那就让整行
一起晚，而不是一半晚一半不晚。

## 清空覆盖记录

新列加上时是 0，要等下一次刷新才有真实值。覆盖记录还在的话，读取侧会把这些日子当成
「已刷新」去读汇总表，读到一堆 0。所以这里清空覆盖记录：读取回退实时，直到下一次
每小时刷新（minute=20）把最近 31 天重写一遍。

**以后任何给汇总表加列或改口径的 migration 都要做同样的事** —— 覆盖记录是读取侧
「能不能信任汇总表」的唯一依据，表结构变了它就必须作废。

Revision ID: 054_summary_read_cols
Revises: 053_summary_coverage
Create Date: 2026-10-02
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "054_summary_read_cols"
down_revision: str | Sequence[str] | None = "053_summary_coverage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_URGE_COLUMNS = ("in_schedule_count", "urge_count", "important_urge_count", "overdue_count")


def upgrade() -> None:
    op.add_column(
        "product_roi_summary",
        sa.Column("add_cart_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    for col in _URGE_COLUMNS:
        op.add_column(
            "pr_work_progress_summary",
            sa.Column(col, sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
    # 两列存精确值，避免逐日舍入（见模块 docstring）
    op.alter_column(
        "pr_work_progress_summary",
        "like_count",
        type_=sa.Numeric(),
        existing_type=sa.BigInteger(),
        existing_nullable=False,
        postgresql_using="like_count::numeric",
    )
    op.alter_column(
        "product_roi_summary",
        "refund_amount",
        type_=sa.Numeric(),
        existing_type=sa.Numeric(16, 2),
        existing_nullable=False,
    )
    # 见模块 docstring「清空覆盖记录」
    op.execute("DELETE FROM report_summary_coverage")
    print("[054] 汇总表补 5 列 + like_count 改 numeric；覆盖记录已清空，下一次刷新前报表回退实时")


def downgrade() -> None:
    # 与 upgrade 对称：表结构回到 053，按新结构写下的覆盖记录同样作废
    op.execute("DELETE FROM report_summary_coverage")
    op.alter_column(
        "product_roi_summary",
        "refund_amount",
        type_=sa.Numeric(16, 2),
        existing_type=sa.Numeric(),
        existing_nullable=False,
    )
    op.alter_column(
        "pr_work_progress_summary",
        "like_count",
        type_=sa.BigInteger(),
        existing_type=sa.Numeric(),
        existing_nullable=False,
        postgresql_using="round(like_count)::bigint",
    )
    for col in reversed(_URGE_COLUMNS):
        op.drop_column("pr_work_progress_summary", col)
    op.drop_column("product_roi_summary", "add_cart_count")
