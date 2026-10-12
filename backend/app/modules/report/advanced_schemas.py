"""U14 报表进阶读模型 + 写入 Schema。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ----------------------------- 工作进度 ----------------------------- #


class PrWorkProgress(BaseModel):
    pr_id: UUID | None = None
    pr_name: str
    quote_count: int
    in_schedule_count: int
    urge_count: int
    important_urge_count: int
    overdue_count: int
    publish_count: int
    info_complete_count: int
    info_complete_rate: Decimal | None = None
    cancel_count: int
    recall_due_count: int
    recall_success_count: int
    recall_complete_rate: Decimal | None = None
    effective_quote_count: int
    """有效约稿量 = 约稿量 − 召回 − 取消，是完成率/超时率的分母（PRD 第 9 章）。"""
    overdue_rate: Decimal | None = None
    month_complete_rate: Decimal | None = None
    hit_count: int
    hit_rate: Decimal | None = None
    like_count: int
    # F3 聚合金额：读不到时为 null（R1 恒给，见 report/field_mask.py）
    cost: Decimal | None
    cpl: Decimal | None = None


# ----------------------------- 下钻单据 ----------------------------- #


class DrillPromotionRow(BaseModel):
    """报表下钻的一张推广单（7e R1，报表视图设计 2.1）。

    字段是白名单：**不返回**收件信息、收款码、备注、``source_extra``。
    金额按 F2 逐行裁剪（``report/field_mask.promotion_amounts_visible``），读不到给 null。
    """

    id: UUID
    internal_code: str
    cooperation_date: date
    scheduled_publish_date: date | None
    actual_publish_date: date | None
    pr_id: UUID | None
    pr_name: str
    blogger_nickname: str
    platform: str
    display_short_name: str
    publish_status: str
    recall_status: str
    quote_amount: Decimal | None
    total_promo_cost: Decimal | None
    like_count: int | None
    # R1 ~ R7 恒 null（详情抽屉随流程线 PR-4，R8 补）
    detail_url: str | None = None


class DrillPromotionPage(BaseModel):
    items: list[DrillPromotionRow]
    total: int
    page: int
    page_size: int


# ----------------------------- 爆款约篇 ----------------------------- #


class TargetCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    pr_id: UUID
    style_id: UUID
    period_month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    min_target: int = Field(..., ge=0)


class TargetWithActual(BaseModel):
    id: UUID
    pr_id: UUID
    pr_name: str
    style_id: UUID
    style_code: str
    style_name: str
    period_month: str
    min_target: int
    actual_count: int
    status: str
    gap: int


# ----------------------------- 店铺数据 ----------------------------- #


class StoreDailyRow(BaseModel):
    date: date
    visitors: int
    pay_amount: Decimal
    pay_orders: int
    ad_spend_total: Decimal | None = None
    zhitongche_spend: Decimal | None = None
    yinli_spend: Decimal | None = None
    # 千牛日报的其余指标（对齐 final.xlsx 店铺数据）：extra 按 extra_metrics 规则聚合，
    # 多行时算不出来的比率 / 均值为 None；按周 / 月 / 年时 date 是桶首日
    extra: dict = Field(default_factory=dict)


class StoreDailyManualUpdate(BaseModel):
    ad_spend_total: Decimal | None = None
    zhitongche_spend: Decimal | None = None
    yinli_spend: Decimal | None = None
    remark: str | None = None


class StoreReportRow(BaseModel):
    """店铺日 / 周 / 月一行（7e R4，设计 2.4）。由 ``report/store_buckets.py`` 从日行算出。

    ``date_from`` / ``date_to`` 是行上的实际起止 = 桶（自然日 / ISO 周 / 自然月）∩ 请求区间，
    日行两者相同。可加列先按天算好再求和（站内推广费逐天套默 13），比率在求和之后现算。
    公式见 PRD §9 + Q17；「其中直播」两列只展示，不参与任何公式。
    """

    date_from: date
    date_to: date
    visitors: int
    pay_amount: Decimal
    pay_orders: int
    refund_amount: Decimal
    brushing_amount: Decimal
    actual_sales: Decimal
    """实际销售额 = 支付 − 退款 − 刷单本金。"""
    ad_spend: Decimal
    """万相台合计。"""
    # 手填三项：整桶没填为 None（页面显示「—」），填了几天就加那几天
    ad_spend_total: Decimal | None = None
    zhitongche_spend: Decimal | None = None
    yinli_spend: Decimal | None = None
    internal_spend: Decimal
    """站内推广费：逐天判断，某天手填了三项任一就用三项之和（默 13），否则万相台。"""
    promo_cost: Decimal
    """站外推广成本（全店已发布推广单）。"""
    total_spend: Decimal
    live_pay_amount: Decimal
    live_ad_spend: Decimal
    refund_only_amount: Decimal | None = None
    """仅退款：只加已录入的日子；整桶都没录为 None。"""
    total_spend_ratio: Decimal | None = None
    """推广总占比 = 推广总额 ÷ 支付总额。"""
    internal_ratio: Decimal | None = None
    external_ratio: Decimal | None = None
    net_spend_ratio: Decimal | None = None
    """净推广占比 = 推广总额 ÷ 实际销售额。"""
    net_roi: Decimal | None = None
    """净投产 = 实际销售额 ÷ 推广总额。"""
    roi_ex_brushing: Decimal | None = None
    """投产（扣除刷单）=（支付总额 − 刷单本金）÷ 推广总额。"""
    refund_only_rate: Decimal | None = None
    """仅退款率 = Σ仅退款 ÷ Σ支付，分子分母都只取已录仅退款的日子（默 14）。"""
    refund_only_days: int
    """「已录 x / y 天」的 x。"""
    day_count: int
    """「已录 x / y 天」的 y：桶内有日行的日子数（没有日行的日子在日表里录不了）。"""
    extra: dict = Field(default_factory=dict)


class StoreReport(BaseModel):
    """``GET /api/reports/store/{daily|weekly|monthly}`` 的响应（一个响应只有一种来源）。"""

    granularity: Literal["day", "week", "month"]
    date_from: date
    date_to: date
    items: list[StoreReportRow]
    recorded_days: int
    """整段已录仅退款的天数（日表标题旁「已录 x / y 天」）。"""
    total_days: int


# ----------------------------- 投产报表 ----------------------------- #


class ProductionRow(BaseModel):
    """投产报表一行 = 商品 × 渠道（7e R3，设计 2.3），套装合并成一行。

    商品没有直播链接时只有「普通」一行；同渠道多条链接合在一行（``link_count``）。
    站外推广成本与刷单都记在「普通」行。
    """

    goods_id: UUID
    goods_code: str
    goods_title: str
    goods_short_name: str | None = None
    is_suit: bool = False
    # 商品含哪些款式货号；单品就是它自己那一个，套装是多个
    style_codes: list[str] = Field(default_factory=list)
    main_image_url: str | None = None
    # None 只出现在 ProductionService.get_goods_rows 的按商品合计行（BI / 企微 / AI 用）
    channel: Literal["普通", "直播"] | None = None
    # 该商品该渠道启用的千牛链接数（当前状态，不看区间）；按商品合计行为 0
    link_count: int = 0
    pay_amount: Decimal
    refund_amount: Decimal
    return_rate: Decimal | None = None
    confirmed_amount: Decimal
    promo_cost: Decimal
    ad_spend: Decimal
    total_spend: Decimal
    # 千牛「支付件数」，刷单开关不扣件数
    pay_orders: int
    add_cart_count: int
    add_cart_cost: Decimal | None = None
    # 站内加购 = 万相台「总购物车数」；站内加购成本 = 站内投放 / 站内加购
    ad_add_cart_count: int
    ad_add_cart_cost: Decimal | None = None
    # 站外加购 = max(Σ配对日总加购 − Σ配对日站内加购, 0)；区间内没有配对日 → None
    offsite_add_cart: int | None = None
    # 退货前投产比 = 支付金额 / 总推广成本
    pre_return_roi: Decimal | None = None
    net_roi: Decimal | None = None
    # 推广单件成交成本 = 总推广成本 / 支付件数（7e R3 起，字段名沿用）
    unit_deal_cost: Decimal | None = None
    # 近 7 天（T-7 ~ T-1，租户时区，不随筛选变；区间见 ProductionReport.recent7_range），
    # 同刷单开关。只挂在本期行：近 7 天没有数据 → 0.00 / 0.00 / None；上期行三列都是 None
    recent7_pay_amount: Decimal | None = None
    recent7_total_spend: Decimal | None = None
    recent7_net_roi: Decimal | None = None
    # 毛利三列（默 12，只给管理员；其他角色 None）：销售货品总成本 = 支付件数 × 当前单品货品成本
    # （启用成员 single_goods_cost 之和，读取时现算）；单品毛利 = 实际销售金额 − 总成本；
    # 单品利润率 = 毛利 / 实际销售金额。任一启用成员没有成本 → 三列都 None
    goods_cost_total: Decimal | None = None
    gross_profit: Decimal | None = None
    gross_margin_rate: Decimal | None = None
    # 千牛/站内导入数据按商品汇总的其余指标（对齐 final.xlsx 投产报表 70 列；
    # qianniu_daily.extra + ad_daily.extra 按 platform_product→商品归集，按 extra_metrics 规则聚合）
    extra: dict = Field(default_factory=dict)


class Recent7Range(BaseModel):
    """近 7 天三列的区间：T-7 ~ T-1（租户时区的今天往前 7 天，今天不算）。"""

    date_from: date
    date_to: date


class ProductionReport(BaseModel):
    items: list[ProductionRow]
    previous: list[ProductionRow] | None = None
    # get_report 恒给；AI 内部包一层按商品合计的行时不给
    recent7_range: Recent7Range | None = None
    # 本期区间内有日报行或花费行的停用链接数（7e-9，页面「N 条链接已停用、未计入单品」）；
    # 两路共用同一条实时 SQL（不读汇总表），只算本期、不随刷单开关。AI 包装不给 → 0
    inactive_link_count: int = 0


class ProductionTrendPoint(BaseModel):
    """单个商品的趋势桶；date 为日值或周/月/年桶的起始日期。"""

    model_config = ConfigDict(from_attributes=True)

    date: date
    pay_amount: Decimal = Decimal("0")
    refund_amount: Decimal = Decimal("0")
    confirmed_amount: Decimal = Decimal("0")
    promo_cost: Decimal = Decimal("0")
    ad_spend: Decimal = Decimal("0")
    total_spend: Decimal = Decimal("0")
    net_roi: Decimal | None = None


class ProductionTrend(BaseModel):
    points: list[ProductionTrendPoint]


# --------------------------- 汇总表数据新鲜度 --------------------------- #


class ReportFreshnessOut(BaseModel):
    """一个报表区间的数据从哪来、截至什么时候，以及当前用户能不能手动刷新。

    独立成一个端点而不是塞进各报表的响应：工作进度、店铺数据两个接口返回的是裸列表，
    加字段就要改成信封结构 —— 前后端分开部署，切换那几分钟里旧前端会直接白屏。
    """

    date_from: date
    date_to: date
    source: Literal["summary", "live"]
    data_as_of: datetime | None = None
    can_refresh: bool


# --------------------------- 报表能力（7e R1） --------------------------- #


class ReportCapabilities(BaseModel):
    """当前用户在报表侧能看 / 能做什么（报表视图设计第 4 节「能力端点」）。

    ``/me`` 只返回角色，前端算不了 scope：菜单、导出 / 刷新按钮、BI 渲染哪一版都看它。
    ``all`` / ``full`` = 看全部，``own`` / ``personal`` = 只看本人，``None`` = 无权。
    """

    work_progress: Literal["all", "own"] | None
    publish_progress: Literal["all", "own"] | None
    bi: Literal["full", "personal"] | None
    production: bool
    production_margin: bool
    """单品投产毛利三列给不给（= ``production`` 且字段规则 F1 可见，只管理员）；false 时页面不渲染这三列。"""
    store_read: bool
    store_write: bool
    export: list[str]
    """能导出的报表类型（``/api/reports/{type}/export`` 的 type）：持导出权且有该报表读权限。"""
    refresh: bool


# ----------------------------- BI 看板 ----------------------------- #


class BiStoreSummary(BaseModel):
    sales_amount: Decimal
    refund_amount: Decimal
    return_rate: Decimal | None = None
    internal_spend: Decimal
    external_spend: Decimal
    total_spend: Decimal
    internal_spend_ratio: Decimal | None = None
    external_spend_ratio: Decimal | None = None
    roi: Decimal | None = None


class BiPromotionSummary(BaseModel):
    # 4 个金额过 F3（field_mask.aggregate_promo_amounts_visible）：R1 恒有值，收紧时为 null
    commission_amount: Decimal | None
    commission_count: int
    published_spend: Decimal | None
    published_count: int
    unpublished_spend: Decimal | None
    unpublished_count: int
    cancelled_amount: Decimal | None
    cancelled_count: int
    publish_rate: Decimal | None = None


class BiWorkloadRow(BaseModel):
    pr_id: UUID | None = None
    pr_name: str
    target_count: int
    quote_count: int
    quote_progress: Decimal | None = None
    publish_count: int
    publish_progress: Decimal | None = None
    pending_count: int
    cancel_count: int
    overdue_count: int


class BiStylePerformance(BaseModel):
    """BI 看板的商品表现行；沿用类名但主体已是商品（套装合并）。"""

    goods_id: UUID
    goods_code: str
    goods_title: str
    goods_short_name: str | None = None
    is_suit: bool = False
    style_codes: list[str] = Field(default_factory=list)
    """成员款号（来自 ``ProductionRow.style_codes``）；图表标签同名时用它区分（补充 2，§11.1）。"""
    main_image_url: str | None = None
    sales_amount: Decimal
    refund_amount: Decimal
    return_rate: Decimal | None = None
    confirmed_amount: Decimal
    internal_spend: Decimal
    external_spend: Decimal
    total_spend: Decimal
    roi: Decimal | None = None


class BiTrendPoint(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    date: date
    sales_amount: Decimal = Decimal("0")
    refund_amount: Decimal = Decimal("0")
    internal_spend: Decimal = Decimal("0")
    external_spend: Decimal = Decimal("0")


class BiDashboard(BaseModel):
    date_from: date
    date_to: date
    granularity: str
    store_summary: BiStoreSummary
    promotion_summary: BiPromotionSummary
    workload: list[BiWorkloadRow]
    style_performance: list[BiStylePerformance]
    trend: list[BiTrendPoint]
    # 兼容 U17 旧客户端；新前端只消费上面的结构化字段。
    cards: list[dict] = Field(default_factory=list)
    charts: list[dict] = Field(default_factory=list)


__all__ = [
    "BiDashboard",
    "BiPromotionSummary",
    "BiStoreSummary",
    "BiStylePerformance",
    "BiTrendPoint",
    "BiWorkloadRow",
    "DrillPromotionPage",
    "DrillPromotionRow",
    "PrWorkProgress",
    "ProductionReport",
    "ProductionRow",
    "ProductionTrend",
    "ProductionTrendPoint",
    "ReportCapabilities",
    "ReportFreshnessOut",
    "StoreDailyManualUpdate",
    "StoreDailyRow",
    "TargetCreate",
    "TargetWithActual",
]
