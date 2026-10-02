"""汇总表刷新覆盖记录：哪些日子被完整刷新过。

报表读取要从实时聚合切到 052 建的汇总表。切之前必须能回答一个问题：
**某天在汇总表里没有行，是那天确实没数据，还是那天从来没刷新过？**

这两种情况在汇总表本身里长得一模一样（都是「没有行」），不区分的话，没刷新过的
历史区间会**静默显示成 0** —— 不报错、看起来像「那段时间没卖出东西」。

生产实测（上线后第一次刷新）：汇总表覆盖 2026-09-17 ~ 09-18，而千牛日报从
2026-01-01 开始、推广单从 2025-12 开始。如果此时直接切读取，选「上个月」以前的
任何区间都会是空的。

``report_summary_coverage`` 给每次刷新区间内的**每一天**记一行，不论那天有没有数据。
读取侧据此判断请求区间是否被完整覆盖：覆盖了读汇总表，没覆盖回退实时聚合。

一行一天而不是存区间（lo, hi）：区间要做合并与重叠判断，按天存则「请求区间是否被
覆盖」就是一句 ``count(*) = 区间天数``。一年 365 行，没有体量问题。

Revision ID: 053_summary_coverage
Revises: 052_report_summary
Create Date: 2026-10-02
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql

revision: str = "053_summary_coverage"
down_revision: str | Sequence[str] | None = "052_report_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_summary_coverage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("stat_date", sa.Date(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "uq_report_summary_coverage",
        "report_summary_coverage",
        ["tenant_id", "stat_date"],
        unique=True,
    )
    op.execute(enable_rls_sql("report_summary_coverage"))

    # **刻意不回填**。看似可以按 052 已有的汇总行反推「哪些日子刷过」，但那是错的：
    # 覆盖的含义是「这天被刷新过」，与这天有没有数据无关。生产上第一次刷新的窗口是
    # 31 天，而有汇总行的只有 2 天 —— 按行反推会把另外 29 个「刷过但没数据」的日子
    # 当成没刷过，也就是说反推出来的覆盖本身就是不完整的。
    #
    # 不回填的代价只是「多走几次实时聚合」：上线后第一次每小时刷新（minute=20）就会把
    # 最近 31 天写全；在那之前读取侧回退实时，数字正确，只是慢一点。
    print("[053] report_summary_coverage 已创建（含 RLS）；首次刷新后写入覆盖记录")


def downgrade() -> None:
    op.execute(disable_rls_sql("report_summary_coverage"))
    op.drop_table("report_summary_coverage")
