"""7e R2：报表视图的汇总表一次迁移（报表视图设计 3.1）。

## 逐表
- ``product_roi_summary``：加 ``channel``（普通 / 直播，默认普通），唯一键重建为
  ``(tenant_id, goods_main_id, channel, stat_date)``；加 ``pay_orders``、``ad_add_cart_count``、
  逐日配对加购 ``paired_all_cart`` / ``paired_ad_cart`` / ``paired_days``。
- ``ad_daily``：加 typed 列 ``add_cart_count``，从 ``extra->>'总购物车数'`` 回填。
- ``shop_daily_summary`` / ``shop_week_summary`` / ``shop_month_summary``：各加 6 列
  （退款不限精度同 054，其余 numeric(16,2)）。
- ``pr_work_progress_summary``：加归属商品 ``goods_main_id``（FK CASCADE，可空），唯一键重建为
  ``(tenant_id, pr_id, goods_main_id, stat_date) NULLS NOT DISTINCT``（原生 DDL，ORM 不声明）；
  加 6 个金额列与 3 个计数列。
- ``store_daily``：加手填的 ``refund_only_amount``（不进汇总）。
- 清空 ``report_summary_coverage``（全部租户）：表结构与口径都变了，覆盖记录作废，读取回退实时，
  下一次每小时刷新把最近 31 天重写一遍（054 定下的规则）。

旧行不删：``product_roi_summary`` 旧行渠道记普通、``pr_work_progress_summary`` 旧行商品为空，
覆盖清了读不到，下一次刷新区间删 + 插替换掉；31 天外的旧行留着无害。

## 回填规则
与 ``importer/adapters/lenient_parse.py::to_optional_int`` 同一规则：去首尾空白与千分位后必须是
整数（接受 ``5.0`` / ``5.``，与 adapter 一致；``5.5``、``-``、空串、``1.2万`` → NULL）。
写成 ``CASE`` 而不是直接 ``CAST``：一个脏值就会让整条 UPDATE 失败、迁移回滚。
062 的状态测试逐值对拍本 SQL 与 adapter。

## 下行（有损）
先删两张表里带新维度的行（``channel = '直播'``、``goods_main_id IS NOT NULL``，汇总可重算），
``store_daily.refund_only_amount`` 是手填，先把非空行以 JSON 打印到迁移日志再删列；
然后删列、恢复旧唯一键、清覆盖。

Revision ID: 062_report_view_columns
Revises: 061_report_view_permissions
Create Date: 2026-10-11
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "062_report_view_columns"
down_revision: str | Sequence[str] | None = "061_report_view_permissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 打印前缀。改迁移编号时与 revision、docstring 一起改
_LOG_PREFIX = "062"

_ZERO = sa.text("0")

_ROI_BIGINT_COLUMNS = ("pay_orders", "ad_add_cart_count", "paired_all_cart", "paired_ad_cart")
_SHOP_TABLES = ("shop_daily_summary", "shop_week_summary", "shop_month_summary")
_SHOP_MONEY_COLUMNS = (
    "brushing_amount",
    "ad_spend",
    "promo_cost",
    "live_pay_amount",
    "live_ad_spend",
)
_PR_MONEY_COLUMNS = (
    "quote_amount",
    "published_quote_amount",
    "effective_quote_amount",
    "unpublished_quote_amount",
    "cancelled_quote_amount",
    "promo_cost",
)
_PR_COUNT_COLUMNS = ("unpublished_count", "shipped_count", "shipped_published_count")

_PR_GOODS_FK = "fk_pr_work_progress_summary_goods_main_id_goods_main"

# 见模块 docstring「回填规则」
_BACKFILL_SQL = """
UPDATE ad_daily
SET add_cart_count = CASE
      WHEN replace(btrim(extra->>'总购物车数'), ',', '') ~ '^[+-]?[0-9]+([.]0*)?$'
      THEN CAST(CAST(replace(btrim(extra->>'总购物车数'), ',', '') AS numeric) AS integer)
    END
WHERE extra IS NOT NULL
"""


def _log(msg: str) -> None:
    print(f"[{_LOG_PREFIX}] {msg}")


def _count(bind: sa.Connection, sql: str) -> int:
    return int(bind.execute(sa.text(sql)).scalar_one())


def _money(name: str) -> sa.Column:
    return sa.Column(name, sa.Numeric(16, 2), nullable=False, server_default=_ZERO)


def upgrade() -> None:
    bind = op.get_bind()

    # 1. product_roi_summary：渠道维度 + 5 列
    op.add_column(
        "product_roi_summary",
        sa.Column("channel", sa.String(16), nullable=False, server_default=sa.text("'普通'")),
    )
    # 命名约定套前缀：库里实名 ck_product_roi_summary_channel
    op.create_check_constraint("channel", "product_roi_summary", "channel IN ('普通', '直播')")
    op.drop_index("uq_product_roi_summary", table_name="product_roi_summary")
    op.create_index(
        "uq_product_roi_summary",
        "product_roi_summary",
        ["tenant_id", "goods_main_id", "channel", "stat_date"],
        unique=True,
    )
    for col in _ROI_BIGINT_COLUMNS:
        op.add_column(
            "product_roi_summary",
            sa.Column(col, sa.BigInteger(), nullable=False, server_default=_ZERO),
        )
    op.add_column(
        "product_roi_summary",
        sa.Column("paired_days", sa.Integer(), nullable=False, server_default=_ZERO),
    )
    roi_rows = _count(bind, "SELECT count(*) FROM product_roi_summary")
    _log(f"product_roi_summary 现有 {roi_rows} 行，渠道记普通")

    # 2. ad_daily：typed 加购列 + 回填
    op.add_column("ad_daily", sa.Column("add_cart_count", sa.Integer(), nullable=True))
    backfilled = bind.execute(sa.text(_BACKFILL_SQL)).rowcount
    non_null = _count(bind, "SELECT count(*) FROM ad_daily WHERE add_cart_count IS NOT NULL")
    _log(f"ad_daily.add_cart_count 回填 {backfilled} 行，非空 {non_null} 行")

    # 3. 店铺三张汇总表：6 列
    for table in _SHOP_TABLES:
        op.add_column(
            table,
            sa.Column("refund_amount", sa.Numeric(), nullable=False, server_default=_ZERO),
        )
        for col in _SHOP_MONEY_COLUMNS:
            op.add_column(table, _money(col))

    # 4. pr_work_progress_summary：商品维度 + 9 列
    op.add_column(
        "pr_work_progress_summary",
        sa.Column("goods_main_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        _PR_GOODS_FK,
        "pr_work_progress_summary",
        "goods_main",
        ["goods_main_id"],
        ["id"],
        ondelete="CASCADE",
    )
    # 可空列上的唯一键：NULLS NOT DISTINCT 才能让「未分配 PR」「归属不到商品」的行唯一
    op.drop_index("uq_pr_work_progress_summary", table_name="pr_work_progress_summary")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_pr_work_progress_summary
        ON pr_work_progress_summary (tenant_id, pr_id, goods_main_id, stat_date)
        NULLS NOT DISTINCT
        """
    )
    for col in _PR_MONEY_COLUMNS:
        op.add_column("pr_work_progress_summary", _money(col))
    for col in _PR_COUNT_COLUMNS:
        op.add_column(
            "pr_work_progress_summary",
            sa.Column(col, sa.Integer(), nullable=False, server_default=_ZERO),
        )
    pr_rows = _count(bind, "SELECT count(*) FROM pr_work_progress_summary")
    _log(f"pr_work_progress_summary 现有 {pr_rows} 行，商品为空")

    # 5. store_daily：手填仅退款
    op.add_column("store_daily", sa.Column("refund_only_amount", sa.Numeric(12, 2), nullable=True))

    # 6. 清覆盖（见模块 docstring）
    cleared = bind.execute(sa.text("DELETE FROM report_summary_coverage")).rowcount
    _log(f"report_summary_coverage 清掉 {cleared} 行，下一次刷新前报表回退实时")


def downgrade() -> None:
    bind = op.get_bind()

    # 1. store_daily：手填值先打印备份再删列
    backup = [
        {
            "tenant_id": str(r.tenant_id),
            "date": r.date.isoformat(),
            "refund_only_amount": str(r.amount),
        }
        for r in bind.execute(
            sa.text(
                "SELECT tenant_id, date, refund_only_amount AS amount FROM store_daily "
                "WHERE refund_only_amount IS NOT NULL ORDER BY tenant_id, date"
            )
        )
    ]
    _log(f"refund_only_amount backup: {json.dumps(backup, ensure_ascii=False)}")
    op.drop_column("store_daily", "refund_only_amount")

    # 2. pr_work_progress_summary：删带商品的行 → 删列 → 恢复旧唯一键
    deleted = bind.execute(
        sa.text("DELETE FROM pr_work_progress_summary WHERE goods_main_id IS NOT NULL")
    ).rowcount
    _log(f"pr_work_progress_summary 删掉带商品的 {deleted} 行")
    op.drop_index("uq_pr_work_progress_summary", table_name="pr_work_progress_summary")
    for col in reversed(_PR_COUNT_COLUMNS + _PR_MONEY_COLUMNS):
        op.drop_column("pr_work_progress_summary", col)
    op.drop_constraint(_PR_GOODS_FK, "pr_work_progress_summary", type_="foreignkey")
    op.drop_column("pr_work_progress_summary", "goods_main_id")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_pr_work_progress_summary
        ON pr_work_progress_summary (tenant_id, pr_id, stat_date)
        NULLS NOT DISTINCT
        """
    )

    # 3. 店铺三张汇总表
    for table in _SHOP_TABLES:
        for col in reversed(_SHOP_MONEY_COLUMNS):
            op.drop_column(table, col)
        op.drop_column(table, "refund_amount")

    # 4. ad_daily
    op.drop_column("ad_daily", "add_cart_count")

    # 5. product_roi_summary：删直播行 → 删列 → 恢复旧唯一键
    deleted = bind.execute(
        sa.text("DELETE FROM product_roi_summary WHERE channel = '直播'")
    ).rowcount
    _log(f"product_roi_summary 删掉直播渠道的 {deleted} 行")
    op.drop_index("uq_product_roi_summary", table_name="product_roi_summary")
    op.drop_column("product_roi_summary", "paired_days")
    for col in reversed(_ROI_BIGINT_COLUMNS):
        op.drop_column("product_roi_summary", col)
    op.drop_constraint("channel", "product_roi_summary", type_="check")
    op.drop_column("product_roi_summary", "channel")
    op.create_index(
        "uq_product_roi_summary",
        "product_roi_summary",
        ["tenant_id", "goods_main_id", "stat_date"],
        unique=True,
    )

    # 6. 回到 061 后读取按旧结构，按新结构写下的覆盖记录同样作废
    cleared = bind.execute(sa.text("DELETE FROM report_summary_coverage")).rowcount
    _log(f"report_summary_coverage 清掉 {cleared} 行")
