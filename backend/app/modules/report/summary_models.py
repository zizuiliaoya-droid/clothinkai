"""报表中间汇总表 ORM（migration 052 / PRD V1.4 模块三）。

这五张表存的是**预聚合结果**，不是业务数据源 —— 删光重建只丢性能不丢数据。
刷新由 ``summary_refresh_service`` 复用 ``advanced_repository`` 的方法完成，不另写
聚合 SQL（理由见 052 migration 的 docstring：口径两处定义是这个项目反复付过代价的坑）。

模型只负责表结构映射。所有比率（net_roi / 各种 rate）不落盘，由 service 层的
``safe_div`` 统一；``urge_status`` 派生的 5 个计数也不在这里 —— 它们依赖「今天」，
按历史日期固化下来永远是错的。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import TenantScopedModel

# 汇总是加法，位数只会涨：源列是 numeric(12,2)，这里留到 (16,2)。
# numeric 溢出会报错而不是静默截断，宁可多留几位。
_MONEY = Numeric(16, 2)
_ZERO = text("0")


def _money() -> Mapped[Decimal]:
    return mapped_column(_MONEY, nullable=False, server_default=_ZERO)


def _count() -> Mapped[int]:
    return mapped_column(Integer, nullable=False, server_default=_ZERO)


class ProductRoiSummary(TenantScopedModel):
    """(商品, 日) 的投产指标。

    刷新源：``ProductionRepository.daily_trend_by_goods(goods_id=None)``。

    ``pay_amount`` 是**含刷单的原值**，``brushing_amount`` 单独存。报表的
    「含刷单 / 剔刷单」开关因此只需一份行：
    含刷单 = ``pay_amount``，剔刷单 = ``pay_amount - brushing_amount``。
    """

    __tablename__ = "product_roi_summary"

    goods_main_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("goods_main.id", ondelete="CASCADE"),
        nullable=False,
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    pay_amount: Mapped[Decimal] = _money()
    brushing_amount: Mapped[Decimal] = _money()
    refund_amount: Mapped[Decimal] = _money()
    promo_cost: Mapped[Decimal] = _money()
    ad_spend: Mapped[Decimal] = _money()
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_product_roi_summary",
            "tenant_id",
            "goods_main_id",
            "stat_date",
            unique=True,
        ),
        Index("idx_product_roi_summary_date", "tenant_id", "stat_date"),
    )


class PrWorkProgressSummary(TenantScopedModel):
    """(PR, 日) 的工作进度计数。

    刷新源：``WorkProgressRepository.aggregate_by_pr`` 逐日调用。

    只存与「今天」无关的计数。档期内 / 催发 / 重要催发 / 超时这 4 个由
    ``urge_status`` 派生，今天算催发的单子明天变超时，固化到历史日期上就是错的，
    读取时实时算再与这里的结果合并。
    """

    __tablename__ = "pr_work_progress_summary"

    # 可为空：未分配 PR 的单据也要进统计（实时聚合里显示为「未分配」）。
    # 唯一索引用 NULLS NOT DISTINCT 才能让这一行可被 upsert 命中，
    # 普通唯一索引对 NULL 不生效，每次刷新都会新增一条。
    pr_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=True,
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    quote_count: Mapped[int] = _count()
    publish_count: Mapped[int] = _count()
    info_complete_count: Mapped[int] = _count()
    cancel_count: Mapped[int] = _count()
    recall_due_count: Mapped[int] = _count()
    recall_success_count: Mapped[int] = _count()
    # 完成率 / 超时率的分母：约稿量扣掉召回与取消，由实时聚合用 FILTER 一次算出
    # （相减会把「既取消又召回过」的单据扣两次）
    effective_quote_count: Mapped[int] = _count()
    hit_count: Mapped[int] = _count()
    like_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=_ZERO)
    cost: Mapped[Decimal] = _money()
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # 唯一索引由 migration 用原生 DDL 建（需要 NULLS NOT DISTINCT，
        # SQLAlchemy 的 Index(unique=True) 表达不了），这里不重复声明，
        # 否则 create_all 会建出一个语义不同的索引。
        Index("idx_pr_work_progress_summary_date", "tenant_id", "stat_date"),
    )


class _ShopSummaryBase(TenantScopedModel):
    """店铺汇总的公共指标。

    三个指标都是可加的，所以周/月表可以直接从日表二次聚合，不必另写分桶 SQL。

    ``store_daily`` 的手填值（3 个广告消耗 + 备注）**不在这里** —— 那是人工
    override 层，读取时 LEFT JOIN 过去，填完立刻生效，不用等下次刷新。
    """

    __abstract__ = True

    visitors: Mapped[int] = _count()
    pay_amount: Mapped[Decimal] = _money()
    pay_orders: Mapped[int] = _count()
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShopDailySummary(_ShopSummaryBase):
    """店铺按日。刷新源：``StoreDailyRepository.aggregate``。

    名字带 ``_summary`` 是刻意的：018 建的 ``store_daily`` 是手填 override 层，
    语义完全不同，同名两张表迟早被人混用。
    """

    __tablename__ = "shop_daily_summary"

    stat_date: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (Index("uq_shop_daily_summary", "tenant_id", "stat_date", unique=True),)


class ShopWeekSummary(_ShopSummaryBase):
    """店铺按 ISO 周。刷新源：``shop_daily_summary`` 二次聚合。

    ``week_start`` 存桶首日（``date_trunc('week')`` 的结果），不存 'YYYY-Www'
    字符串 —— 字符串的排序与区间筛选都要额外转换。
    """

    __tablename__ = "shop_week_summary"

    week_start: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (Index("uq_shop_week_summary", "tenant_id", "week_start", unique=True),)


class ShopMonthSummary(_ShopSummaryBase):
    """店铺按月。刷新源：``shop_daily_summary`` 二次聚合。

    ``period_month`` 是 ``date``（存月初），不是 ``target_planning.period_month``
    那种 'YYYY-MM' 字符串 —— 那是那张表的历史格式，不该传染过来。
    """

    __tablename__ = "shop_month_summary"

    period_month: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (Index("uq_shop_month_summary", "tenant_id", "period_month", unique=True),)


__all__ = [
    "PrWorkProgressSummary",
    "ProductRoiSummary",
    "ShopDailySummary",
    "ShopMonthSummary",
    "ShopWeekSummary",
]
