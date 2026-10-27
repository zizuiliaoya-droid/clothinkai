"""报表中间汇总表 ORM（migration 052 / PRD V1.4 模块三）。

这三张日汇总表存的是**预聚合结果**，不是业务数据源 —— 删光重建只丢性能不丢数据。
刷新由 ``summary_refresh_service`` 复用 ``advanced_repository`` 的方法完成，不另写
聚合 SQL（理由见 052 migration 的 docstring：口径两处定义是这个项目反复付过代价的坑）。

模型只负责表结构映射。所有比率（net_roi / 各种 rate）不落盘，由 service 层的
``safe_div`` 统一 —— 落盘会把除零语义固化进数据。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

# FK 指向的表必须在同一份 metadata 里注册，否则 flush 时抛 NoReferencedTableError。
#
# HTTP 路径碰不到（app 启动时 router 链式 import 了全部 models），但 Celery worker
# 的 import 链是 celery_app → summary_tasks → summary_refresh_service → 本模块，
# 到不了 product / auth 的 models —— 生产第一次跑刷新任务就栽在这里。
#
# 注意触发点不是 import 也不是 configure_mappers()：FK 的目标表是**懒解析**的，
# 两者都照样成功。真正炸的地方是 flush 要给相关表排序时（mapper._sort_tables →
# fk.column）。所以 tests/unit/test_summary_models_standalone.py 直接查
# 「FK 目标表在不在 metadata 里」，而不是指望 configure_mappers() 报错。
#
# 这两行不是「多余的 import」，是本模块 FK 能解析的前提。
import app.modules.auth.models
import app.modules.product.goods_models  # noqa: F401  (goods_main)
from app.core.db import TenantScopedModel

# 汇总是加法，位数只会涨：源列是 numeric(12,2)，这里留到 (16,2)。
# numeric 溢出会报错而不是静默截断，宁可多留几位。
_MONEY = Numeric(16, 2)
_ZERO = text("0")


def _money() -> Mapped[Decimal]:
    return mapped_column(_MONEY, nullable=False, server_default=_ZERO)


def _count() -> Mapped[int]:
    return mapped_column(Integer, nullable=False, server_default=_ZERO)


def _big_count() -> Mapped[int]:
    return mapped_column(BigInteger, nullable=False, server_default=_ZERO)


class ProductRoiSummary(TenantScopedModel):
    """(商品, 渠道, 日) 的投产指标（062 加渠道维度）。

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
    # 062：销售 / 加购按千牛链接的渠道，站内花费按万相台链接的渠道，推广 / 刷单记普通
    channel: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'普通'"))
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    pay_amount: Mapped[Decimal] = _money()
    brushing_amount: Mapped[Decimal] = _money()
    # 不限精度（054 改的类型）。055 起源列是 qianniu_daily.refund_amount numeric(12,2)，
    # 逐日求和本身已是精确的；保留不限精度只是不必为此再改一次表
    refund_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=_ZERO)
    # 054 补：投产报表「总加购数」「加购成本」两列靠它
    add_cart_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=_ZERO)
    promo_cost: Mapped[Decimal] = _money()
    ad_spend: Mapped[Decimal] = _money()
    # 062 补 5 列：支付件数、站内加购，以及逐日配对的加购（同一商品、渠道、日
    # 千牛与万相台都有行才记，paired_days = 1；否则三列都是 0）
    pay_orders: Mapped[int] = _big_count()
    ad_add_cart_count: Mapped[int] = _big_count()
    paired_all_cart: Mapped[int] = _big_count()
    paired_ad_cart: Mapped[int] = _big_count()
    paired_days: Mapped[int] = _count()
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_product_roi_summary",
            "tenant_id",
            "goods_main_id",
            "channel",
            "stat_date",
            unique=True,
        ),
        Index("idx_product_roi_summary_date", "tenant_id", "stat_date"),
        # 命名约定套前缀，库里实名 ck_product_roi_summary_channel
        CheckConstraint("channel IN ('普通', '直播')", name="channel"),
    )


class PrWorkProgressSummary(TenantScopedModel):
    """(PR, 归属商品, 日) 的工作进度计数（062 加商品维度）。

    刷新源：``WorkProgressRepository.aggregate_by_pr`` 逐日调用。

    档期内 / 催发 / 重要催发 / 超时 4 个计数由 ``urge_status`` 派生、依赖「今天」，
    存的是**刷新时刻**的状态（054 补列，推翻 052 不存的决定，理由见 054 docstring）：
    最近 31 天每小时重算，整行与其他计数同样最多晚一小时；31 天外随历史冻结。
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
    # 062：归属商品（_PROMO_GOODS_LATERAL），可为空 —— 归属不到商品的单据也要进统计。
    # 唯一键 (tenant_id, pr_id, goods_main_id, stat_date) NULLS NOT DISTINCT 同样由迁移建
    goods_main_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("goods_main.id", ondelete="CASCADE"),
        nullable=True,
    )
    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    quote_count: Mapped[int] = _count()
    # 054 补：催发派生计数（截至刷新时刻）
    in_schedule_count: Mapped[int] = _count()
    urge_count: Mapped[int] = _count()
    important_urge_count: Mapped[int] = _count()
    overdue_count: Mapped[int] = _count()
    publish_count: Mapped[int] = _count()
    info_complete_count: Mapped[int] = _count()
    cancel_count: Mapped[int] = _count()
    recall_due_count: Mapped[int] = _count()
    recall_success_count: Mapped[int] = _count()
    # 完成率 / 超时率的分母：约稿量扣掉召回与取消，由实时聚合用 FILTER 一次算出
    # （相减会把「既取消又召回过」的单据扣两次）
    effective_quote_count: Mapped[int] = _count()
    hit_count: Mapped[int] = _count()
    # numeric 而不是整数：抖音/快手点赞按 ×0.1 折算，单日可以是小数。逐日存整数会
    # 「先舍入再求和」，与实时路径「先求和再取整」对不上（054 改的类型）
    like_count: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=_ZERO)
    # 旧列保留、不再展示（062 起展示 promo_cost）
    cost: Mapped[Decimal] = _money()
    # 062 补 9 列：报价金额按发布状态拆，推广费只看 is_active，发货读 ship_status = '已发货'
    quote_amount: Mapped[Decimal] = _money()
    published_quote_amount: Mapped[Decimal] = _money()
    effective_quote_amount: Mapped[Decimal] = _money()
    unpublished_quote_amount: Mapped[Decimal] = _money()
    cancelled_quote_amount: Mapped[Decimal] = _money()
    promo_cost: Mapped[Decimal] = _money()
    unpublished_count: Mapped[int] = _count()
    shipped_count: Mapped[int] = _count()
    shipped_published_count: Mapped[int] = _count()
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # 唯一索引由 migration 用原生 DDL 建（需要 NULLS NOT DISTINCT，
        # SQLAlchemy 的 Index(unique=True) 表达不了），这里不重复声明，
        # 否则 create_all 会建出一个语义不同的索引。
        Index("idx_pr_work_progress_summary_date", "tenant_id", "stat_date"),
    )


class _ShopSummaryBase(TenantScopedModel):
    """店铺汇总的公共指标。

    指标全部可加，当前周/月报表由日行分桶，不另存周/月缓存。

    ``store_daily`` 的手填值（3 个广告消耗 + 仅退款 + 备注）**不在这里** —— 那是人工
    override 层，读取时 LEFT JOIN 过去，填完立刻生效，不用等下次刷新。
    """

    __abstract__ = True

    visitors: Mapped[int] = _count()
    pay_amount: Mapped[Decimal] = _money()
    pay_orders: Mapped[int] = _count()
    # 062 补 6 列（全店口径）。退款不限精度，理由同 054 的 product_roi_summary.refund_amount
    refund_amount: Mapped[Decimal] = mapped_column(Numeric, nullable=False, server_default=_ZERO)
    brushing_amount: Mapped[Decimal] = _money()
    ad_spend: Mapped[Decimal] = _money()
    promo_cost: Mapped[Decimal] = _money()
    live_pay_amount: Mapped[Decimal] = _money()
    live_ad_spend: Mapped[Decimal] = _money()
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ShopDailySummary(_ShopSummaryBase):
    """店铺按日。刷新源：``StoreDailyRepository.aggregate``。

    名字带 ``_summary`` 是刻意的：018 建的 ``store_daily`` 是手填 override 层，
    语义完全不同，同名两张表迟早被人混用。
    """

    __tablename__ = "shop_daily_summary"

    stat_date: Mapped[date] = mapped_column(Date, nullable=False)

    __table_args__ = (Index("uq_shop_daily_summary", "tenant_id", "stat_date", unique=True),)


class ReportSummaryCoverage(TenantScopedModel):
    """哪些日子被完整刷新过（migration 053）。

    汇总表里「某天没有行」可能是那天没数据，也可能是从没刷过。不区分的话，
    没刷过的历史区间会静默显示成 0。每次刷新给区间内**每一天**记一行，
    不论那天有没有数据；读取侧据此决定读汇总表还是回退实时聚合。
    """

    __tablename__ = "report_summary_coverage"

    stat_date: Mapped[date] = mapped_column(Date, nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (Index("uq_report_summary_coverage", "tenant_id", "stat_date", unique=True),)


# 各表落盘的指标列：刷新写入与汇总读取都引这里，不另抄清单。
# 单测断言它们 = 模型列去掉键列与 refreshed_at，加列漏写会红（推广列表漏过 5 列的教训）。
# 比率不落盘，由 service 层的 safe_div 现算。
ROI_METRICS: tuple[str, ...] = (
    "pay_amount",
    "brushing_amount",
    "refund_amount",
    "add_cart_count",
    "promo_cost",
    "ad_spend",
    "pay_orders",
    "ad_add_cart_count",
    "paired_all_cart",
    "paired_ad_cart",
    "paired_days",
)

PR_PROGRESS_METRICS: tuple[str, ...] = (
    "quote_count",
    "in_schedule_count",
    "urge_count",
    "important_urge_count",
    "overdue_count",
    "publish_count",
    "info_complete_count",
    "cancel_count",
    "recall_due_count",
    "recall_success_count",
    "effective_quote_count",
    "hit_count",
    "like_count",
    "cost",
    "quote_amount",
    "published_quote_amount",
    "effective_quote_amount",
    "unpublished_quote_amount",
    "cancelled_quote_amount",
    "promo_cost",
    "unpublished_count",
    "shipped_count",
    "shipped_published_count",
)

SHOP_METRICS: tuple[str, ...] = (
    "visitors",
    "pay_amount",
    "pay_orders",
    "refund_amount",
    "brushing_amount",
    "ad_spend",
    "promo_cost",
    "live_pay_amount",
    "live_ad_spend",
)


__all__ = [
    "PR_PROGRESS_METRICS",
    "ROI_METRICS",
    "SHOP_METRICS",
    "PrWorkProgressSummary",
    "ProductRoiSummary",
    "ReportSummaryCoverage",
    "ShopDailySummary",
]
