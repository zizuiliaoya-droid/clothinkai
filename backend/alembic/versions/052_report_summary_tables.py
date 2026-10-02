"""报表中间汇总表 ×5（PRD V1.4 模块三）。

PRD 要求「中间汇总表（商品ROI汇总 / PR工作进度汇总 / 店铺日/周/月汇总）」+ 每小时增量
刷新。五张表都是**预聚合结果**，不是新的业务数据源 —— 删光重建只会丢性能，不会丢数据。

## 刷新源必须是现有 repository 方法

这五张表的最大风险不是性能也不是调度，而是**预聚合口径和实时查询口径悄悄分叉**：
数字对不上，而且不报错、不抛异常、没有任何信号。这个项目已经为「同一个值两处定义」
付过三次代价（``legacy_settings`` 与 ``scan_service`` 的催发阈值、``like_count`` 与
``source_extra['点赞数']``、推广列表手写列白名单漏 5 列上线后一直是空的）。

所以刷新任务**复用** ``advanced_repository`` 的方法，不另写一条 ``INSERT ... SELECT``：

| 表 | 刷新源 |
|---|---|
| ``product_roi_summary`` | ``ProductionRepository.daily_trend_by_goods(goods_id=None)`` |
| ``pr_work_progress_summary`` | ``WorkProgressRepository.aggregate_by_pr`` 逐日调用 |
| ``shop_daily_summary`` | ``StoreDailyRepository.aggregate`` |
| ``shop_week_summary`` | ``shop_daily_summary`` 二次聚合 |
| ``shop_month_summary`` | ``shop_daily_summary`` 二次聚合 |

周/月表从日表二次聚合而不是各写一条 SQL：周月就是日的加总，可加指标 SUM 一次即可，
口径天然一致，也不必再维护两份分桶逻辑。

## 不落盘的东西

**比率一律不存**（net_roi / 完成率 / 超时率 / 仅退款率…）。落盘会把除零语义固化在
数据里 —— 分母 0 到底存 0、存 NULL 还是不存这一行？现在这个语义由
``services.metric.common.safe_div`` 一处决定，存了就变成两处。

**``urge_status`` 派生的 5 个计数不存**（档期内 / 催发 / 重要催发 / 超时）。它们依赖
``today``：今天算「催发」的单子明天就变「超时」，按历史日期固化下来就永远是错的。
这 5 个继续实时算，读取时与汇总表的结果合并。

**``store_daily`` 的手填值不存**（3 个广告消耗 + 备注）。那张表是人工 override 层，
读取时仍然 LEFT JOIN 它，手填完立刻生效，不必等下一次刷新。

## 刻意多存的一列

``product_roi_summary.brushing_amount``：报表有「含刷单 / 剔刷单」开关。只存一个口径
另一个就查不了，为开关各存一份行则是把同一笔数据写两遍。存刷单额本身，两个口径都能
从一份行还原：含刷单 = ``pay_amount``，剔刷单 = ``pay_amount - brushing_amount``。

## 不在本次范围

``data_source``（api 自动 / excel 手动）与「仅退款率」字段留在批次 5b：
- ``qianniu_daily`` / ``ad_daily`` 既没有 ``import_batch_id`` 也没有来源列，
  **历史数据无法回填**。光加列只会得到一列全 NULL，必须先给两条写入通道各自打标。
- 「仅退款笔数」是否存在于千牛导出里尚未确认，加一个永远为 NULL 的列是负资产。

Revision ID: 052_report_summary
Revises: 051_brand_comment_amount_log
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.security.rls import disable_rls_sql, enable_rls_sql
from app.modules.report.advanced_permissions import REPORT_SUMMARY_PERMISSIONS

revision: str = "052_report_summary"
down_revision: str | Sequence[str] | None = "051_brand_comment_amount_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "product_roi_summary",
    "pr_work_progress_summary",
    "shop_daily_summary",
    "shop_week_summary",
    "shop_month_summary",
)


def _log(msg: str) -> None:
    print(f"[052] {msg}")


def _common_columns() -> list[sa.Column]:
    """id / tenant_id / created_at / updated_at —— 与 TenantScopedModel 对齐。"""
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    ]


def _money(name: str) -> sa.Column:
    """金额列。

    精度用 (16, 2) 而不是源列的 (12, 2)：汇总是加法，位数只会涨。
    numeric 溢出会直接报错而不是静默截断，宁可多留 4 位。
    """
    return sa.Column(
        name, sa.Numeric(16, 2), nullable=False, server_default=sa.text("0")
    )


def _count(name: str) -> sa.Column:
    return sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0"))


def upgrade() -> None:
    # ------------------------------------------------------------------ #
    # 1. product_roi_summary —— (商品, 日)
    # ------------------------------------------------------------------ #
    op.create_table(
        "product_roi_summary",
        *_common_columns(),
        sa.Column("goods_main_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stat_date", sa.Date(), nullable=False),
        # 含刷单的原值。剔刷单口径 = pay_amount - brushing_amount（见模块 docstring）
        _money("pay_amount"),
        _money("brushing_amount"),
        _money("refund_amount"),
        _money("promo_cost"),
        _money("ad_spend"),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        # 商品删除时汇总行一起走：汇总是派生数据，留着指向不存在的商品没有意义
        sa.ForeignKeyConstraint(["goods_main_id"], ["goods_main.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_product_roi_summary",
        "product_roi_summary",
        ["tenant_id", "goods_main_id", "stat_date"],
        unique=True,
    )
    # 报表按区间扫，日期在前比商品在前更有用
    op.create_index(
        "idx_product_roi_summary_date", "product_roi_summary", ["tenant_id", "stat_date"]
    )

    # ------------------------------------------------------------------ #
    # 2. pr_work_progress_summary —— (PR, 日)
    #
    # 只存与 today 无关的计数。urge_status 派生的 5 个（档期内/催发/重要催发/超时）
    # 不在这里 —— 见模块 docstring。
    # ------------------------------------------------------------------ #
    op.create_table(
        "pr_work_progress_summary",
        *_common_columns(),
        # 可为空：未分配 PR 的单据也要进统计（实时聚合里显示为「未分配」）
        sa.Column("pr_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("stat_date", sa.Date(), nullable=False),
        _count("quote_count"),
        _count("publish_count"),
        _count("info_complete_count"),
        _count("cancel_count"),
        _count("recall_due_count"),
        _count("recall_success_count"),
        # 完成率/超时率的分母：约稿量扣掉召回与取消，由实时聚合用 FILTER 一次算出
        _count("effective_quote_count"),
        _count("hit_count"),
        # 点赞总量会很大，用 bigint
        sa.Column("like_count", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        _money("cost"),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pr_id"], ["user.id"], ondelete="CASCADE"),
    )
    # pr_id 可为 NULL，普通唯一索引对 NULL 不生效，会让「未分配」那行每次刷新都新增
    # 一条。NULLS NOT DISTINCT 把 NULL 当成一个值来比（PG 15+，本地 16 / 生产 18）。
    op.execute(
        """
        CREATE UNIQUE INDEX uq_pr_work_progress_summary
        ON pr_work_progress_summary (tenant_id, pr_id, stat_date)
        NULLS NOT DISTINCT
        """
    )
    op.create_index(
        "idx_pr_work_progress_summary_date",
        "pr_work_progress_summary",
        ["tenant_id", "stat_date"],
    )

    # ------------------------------------------------------------------ #
    # 3. shop_daily_summary —— 店铺按日
    #
    # 与 store_daily（018 建）不是同一层：那张表是人工 override（3 个广告消耗 +
    # 备注），读取时仍 LEFT JOIN 它，手填值不进汇总表。两张表同名极易打架，
    # 所以这张叫 _summary。
    # ------------------------------------------------------------------ #
    op.create_table(
        "shop_daily_summary",
        *_common_columns(),
        sa.Column("stat_date", sa.Date(), nullable=False),
        _count("visitors"),
        _money("pay_amount"),
        _count("pay_orders"),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "uq_shop_daily_summary", "shop_daily_summary", ["tenant_id", "stat_date"], unique=True
    )

    # ------------------------------------------------------------------ #
    # 4 & 5. shop_week_summary / shop_month_summary
    #
    # 都从 shop_daily_summary 二次聚合。week_start / period_month 存**桶的首日**
    # （date_trunc 的结果），不存 'YYYY-MM' 字符串 —— 字符串排序和区间筛选都要
    # 额外转换，而 target_planning.period_month 用字符串是它自己的历史格式，
    # 不该传染到这里。
    # ------------------------------------------------------------------ #
    for table, bucket_col in (
        ("shop_week_summary", "week_start"),
        ("shop_month_summary", "period_month"),
    ):
        op.create_table(
            table,
            *_common_columns(),
            sa.Column(bucket_col, sa.Date(), nullable=False),
            _count("visitors"),
            _money("pay_amount"),
            _count("pay_orders"),
            sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenant.id"], ondelete="RESTRICT"),
        )
        op.create_index(f"uq_{table}", table, ["tenant_id", bucket_col], unique=True)

    for table in _TABLES:
        op.execute(enable_rls_sql(table))
    _log(f"已创建 {len(_TABLES)} 张汇总表（含 RLS 与唯一索引）")

    # ------------------------------------------------------------------ #
    # 权限：report.summary:refresh
    #
    # 独立 scope 而不是复用 report.production / report.store_daily ——
    # 手动刷新横跨 5 张表并且先删区间再重建，挂在单张报表的读写权下，
    # 持有那张报表权限的人就能触发全表重算。只授 admin。
    # ------------------------------------------------------------------ #
    bind = op.get_bind()
    for scope, action, name in REPORT_SUMMARY_PERMISSIONS:
        bind.execute(
            sa.text(
                "INSERT INTO permission (id, scope, name, category, created_at, updated_at) "
                "VALUES (gen_random_uuid(), :scope, :name, 'function', NOW(), NOW()) "
                "ON CONFLICT (scope) DO NOTHING"
            ),
            {"scope": f"{scope}:{action}", "name": name},
        )
        res = bind.execute(
            sa.text(
                "INSERT INTO role_permission (id, role_id, permission_id) "
                "SELECT gen_random_uuid(), r.id, p.id FROM role r, permission p "
                "WHERE r.code = 'admin' AND p.scope = :scope "
                "ON CONFLICT (role_id, permission_id) DO NOTHING"
            ),
            {"scope": f"{scope}:{action}"},
        )
        _log(f"权限 {scope}:{action} 入册，admin 授权 {res.rowcount or 0} 条")
    _log("  读取路径本次不切换：先刷新 + 校验数字与实时聚合一致，再切")


def downgrade() -> None:
    bind = op.get_bind()
    scopes = [f"{s}:{a}" for s, a, _ in REPORT_SUMMARY_PERMISSIONS]
    bind.execute(
        sa.text(
            "DELETE FROM role_permission WHERE permission_id IN "
            "(SELECT id FROM permission WHERE scope = ANY(:scopes))"
        ),
        {"scopes": scopes},
    )
    bind.execute(
        sa.text("DELETE FROM permission WHERE scope = ANY(:scopes)"),
        {"scopes": scopes},
    )

    for table in _TABLES:
        op.execute(disable_rls_sql(table))
    # 先建的后删：没有表间 FK，顺序不强制，倒序只为对称可读
    for table in reversed(_TABLES):
        op.drop_table(table)
