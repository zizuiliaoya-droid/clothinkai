// report feature 类型（对齐后端 report schemas）。

/**
 * 工作进度的指标段（7e R5，对齐后端 WorkProgressMetrics）：PR 表、单品卡片、周明细共用。
 * 比率在求和后现算，分母 ≤ 0 为 null；金额列与金额派生的四列（单篇均价、两个金额发布率、CPL）读不到时为 null。
 */
export interface WorkProgressMetrics {
  quote_count: number;
  /** 有效约稿量 = 约稿量 − 召回 − 取消，是完成率/超时率的分母。 */
  effective_quote_count: number;
  in_schedule_count: number;
  urge_count: number;
  important_urge_count: number;
  overdue_count: number;
  publish_count: number;
  info_complete_count: number;
  info_complete_rate: string | null;
  cancel_count: number;
  recall_due_count: number;
  recall_success_count: number;
  recall_complete_rate: string | null;
  overdue_rate: string | null;
  month_complete_rate: string | null;
  hit_count: number;
  hit_rate: string | null;
  like_count: number;
  /** 样品成本之和（旧列，R5 起页面不再展示，接口保留）。 */
  cost: string | null;
  /** CPL = 推广成本 ÷ 折算点赞（R5 起分子换成 promo_cost）。 */
  cpl: string | null;
  quote_amount: string | null;
  published_quote_amount: string | null;
  /** 有效约稿服务费：扣取消与召回（同完成率分母）。 */
  effective_quote_amount: string | null;
  unpublished_quote_amount: string | null;
  cancelled_quote_amount: string | null;
  /** 推广成本（站外）：未删除推广单的应付之和，不看发布 / 召回状态。 */
  promo_cost: string | null;
  unpublished_count: number;
  shipped_count: number;
  shipped_published_count: number;
  /** 发布单篇均价 = 已发布服务费 ÷ 发布量。 */
  avg_published_quote: string | null;
  /** 金额发布率 = 已发布服务费 ÷ 总约稿服务费。 */
  amount_publish_rate: string | null;
  /** 总金额发布率 = 已发布服务费 ÷ 有效约稿服务费。 */
  total_amount_publish_rate: string | null;
  /** 发货发布率 =（已发货且已发布）÷ 已发货。 */
  ship_publish_rate: string | null;
}

export interface PrWorkProgress extends WorkProgressMetrics {
  pr_id: string | null;
  pr_name: string;
}

/** 单品卡片一行（7e R5）：不含商品编码；「未归属商品」行 goods_id / goods_title / goods_short_name 为 null。 */
export interface WorkProgressGoodsRow extends WorkProgressMetrics {
  goods_id: string | null;
  goods_title: string | null;
  goods_short_name: string | null;
  /** 有简称用简称，没填回落全称；未归属 =「未归属商品」。 */
  display_name: string;
}

/** GET /api/reports/work-progress/goods：items / total 只含有归属的商品，未归属的单汇成 unassigned。 */
export interface WorkProgressGoodsPage {
  date_from: string;
  date_to: string;
  total: number;
  limit: number;
  offset: number;
  items: WorkProgressGoodsRow[];
  unassigned: WorkProgressGoodsRow | null;
}

/** 周明细一行（7e R5）：date_from / date_to = 该 ISO 周 ∩ 筛选区间（首尾周被截断）。 */
export interface WorkProgressWeekRow extends WorkProgressMetrics {
  /** ISO 周一（桶键）；首周可能早于 date_from。 */
  week_start: string;
  date_from: string;
  date_to: string;
}

/** GET /api/reports/work-progress/weeks：只出有单的周，升序。 */
export interface WorkProgressWeeks {
  date_from: string;
  date_to: string;
  items: WorkProgressWeekRow[];
}

export interface TargetWithActual {
  id: string;
  pr_id: string;
  pr_name: string;
  style_id: string;
  style_code: string;
  style_name: string;
  period_month: string;
  min_target: number;
  actual_count: number;
  status: string;
  gap: number;
}

export interface ProgressSummary {
  quote_count: number;
  quote_amount: string;
  cooperation_amount: string;
  publish_count: number;
  publish_rate: string | null;
  overdue_count: number;
  overdue_rate: string | null;
  like_count: number;
  cpl: string | null;
  cancel_count: number;
}

export interface StyleCard {
  style_id: string;
  style_code: string;
  style_name: string;
  main_image_key: string | null;
  cost: string;
  quote_count: number;
  quote_amount: string;
  publish_count: number;
  cooperation_amount: string;
  cancel_count: number;
  overdue_count: number;
  like_count: number;
  cpl: string | null;
  publish_rate: string | null;
  overdue_rate: string | null;
}

export interface StyleCardPage {
  items: StyleCard[];
  total: number;
  page: number;
  page_size: number;
}

/** 店铺报表粒度（7e R4）：日明细 / 周汇总 / 月度汇总三页。 */
export type StoreGranularity = "day" | "week" | "month";

/**
 * 店铺日 / 周 / 月一行（7e R4，设计 2.4；对齐后端 StoreReportRow）。
 *
 * `date_from` / `date_to` 是行上的实际起止 = 桶 ∩ 请求区间（日行两者相同）。
 * 比率在分桶求和后现算，分母为 0 / 负时为 null。「其中直播」两列只展示，不参与公式。
 */
export interface StoreReportRow {
  date_from: string;
  date_to: string;
  /** 商品访客数合计：按商品累加，同一访客看了多个商品会重复计。 */
  visitors: number;
  pay_amount: string;
  pay_orders: number;
  refund_amount: string;
  brushing_amount: string;
  actual_sales: string;
  /** 万相台合计。 */
  ad_spend: string;
  /** 手填三项：整桶没填为 null。 */
  ad_spend_total: string | null;
  zhitongche_spend: string | null;
  yinli_spend: string | null;
  /** 站内推广费：逐天判断，手填了三项任一就用三项之和，否则万相台。 */
  internal_spend: string;
  /** 站外推广成本。 */
  promo_cost: string;
  total_spend: string;
  live_pay_amount: string;
  live_ad_spend: string;
  /** 仅退款：只加已录入的日子；整桶都没录为 null。 */
  refund_only_amount: string | null;
  total_spend_ratio: string | null;
  internal_ratio: string | null;
  external_ratio: string | null;
  net_spend_ratio: string | null;
  net_roi: string | null;
  roi_ex_brushing: string | null;
  refund_only_rate: string | null;
  /** 「已录 x / y 天」的 x：桶内已录仅退款的天数。 */
  refund_only_days: number;
  /** 「已录 x / y 天」的 y：桶内有日行的天数。 */
  day_count: number;
  extra?: Record<string, unknown>;
}

/** GET /api/reports/store/{daily|weekly|monthly} 的响应（一个响应只有一种来源）。 */
export interface StoreReport {
  granularity: StoreGranularity;
  date_from: string;
  date_to: string;
  items: StoreReportRow[];
  /** 整段已录仅退款的天数。 */
  recorded_days: number;
  total_days: number;
}

/** PUT /api/reports/store-daily/{day} 的响应：写后的手填值（null = 没填）。 */
export interface StoreDailyManualResult {
  ok: boolean;
  date: string;
  ad_spend_total: string | null;
  zhitongche_spend: string | null;
  yinli_spend: string | null;
  refund_only_amount: string | null;
  remark: string | null;
}

/** 单品投产渠道（7e R3）：一行 = 商品 × 渠道；null 只出现在内部按商品合计的行上。 */
export type ProductionChannel = "普通" | "直播";

/** 单品投产一行（7e R3，设计 2.3）：同商品同渠道的多条链接合成一行，行键 = 商品 id + 渠道。 */
export interface ProductionRow {
  goods_id: string;
  goods_code: string;
  goods_title: string;
  goods_short_name: string | null;
  is_suit: boolean;
  style_codes: string[];
  main_image_url: string | null;
  channel: ProductionChannel | null;
  /** 该商品该渠道启用的千牛链接数（当前状态，不看区间）。 */
  link_count: number;
  pay_amount: string;
  refund_amount: string;
  return_rate: string | null;
  /** 实际销售金额（原「待确认收货金额」，字段名不变）。 */
  confirmed_amount: string;
  promo_cost: string;
  ad_spend: string;
  total_spend: string;
  /** 支付件数：剔除刷单开关不扣件数。 */
  pay_orders: number;
  add_cart_count: number;
  add_cart_cost: string | null;
  ad_add_cart_count: number;
  ad_add_cart_cost: string | null;
  /** 站外加购：所选区间千牛与万相台没有同一天的数据时为 null。 */
  offsite_add_cart: number | null;
  pre_return_roi: string | null;
  net_roi: string | null;
  /** 推广单件成交成本（推广总花费 / 支付件数）。 */
  unit_deal_cost: string | null;
  /** 近 7 天（T-7 ~ T-1，不随筛选变）；上期行为 null。 */
  recent7_pay_amount: string | null;
  recent7_total_spend: string | null;
  recent7_net_roi: string | null;
  /** 毛利三列只给管理员，其他角色恒 null；缺成本也为 null。 */
  goods_cost_total: string | null;
  gross_profit: string | null;
  gross_margin_rate: string | null;
  extra?: Record<string, unknown>;
}

/** 近 7 天区间（租户时区 T-7 ~ T-1）。 */
export interface Recent7Range {
  date_from: string;
  date_to: string;
}

export interface ProductionReport {
  items: ProductionRow[];
  previous: ProductionRow[] | null;
  recent7_range: Recent7Range | null;
  /** 区间内有日报或花费、但已停用的链接数（未计入单品）。 */
  inactive_link_count: number;
}

export type TimePreset =
  | "last_7d"
  | "last_30d"
  | "this_month"
  | "last_month"
  | "custom";

export type TimeGranularity = "day" | "week" | "month" | "year";

export interface ProductionTrendPoint {
  date: string;
  pay_amount: string;
  refund_amount: string;
  confirmed_amount: string;
  promo_cost: string;
  ad_spend: string;
  total_spend: string;
  net_roi: string | null;
}

export interface ProductionTrend {
  points: ProductionTrendPoint[];
}

/**
 * BI 店铺 KPI（7e R6，对齐后端 BiStoreSummary）：整个区间合成一行，公式同店铺日 / 周 / 月（设计 2.4）。
 * 比率分母 ≤ 0 为 null。
 */
export interface BiStoreSummary {
  pay_amount: string;
  refund_amount: string;
  /** 退货率 = 退款 ÷ 支付。 */
  return_rate: string | null;
  brushing_amount: string;
  /** 实际销售额 = 支付 − 退款 − 刷单本金。 */
  actual_sales: string;
  internal_spend: string;
  external_spend: string;
  /** 推广总额 = 站内 + 站外。 */
  total_spend: string;
  /** 推广总占比 = 推广总额 ÷ 支付总额。 */
  total_spend_ratio: string | null;
  /** 站内占比 = 站内 ÷ 实际销售额。 */
  internal_spend_ratio: string | null;
  /** 站外占比 = 站外 ÷ 实际销售额。 */
  external_spend_ratio: string | null;
  /** 净投产 = 实际销售额 ÷ 推广总额。 */
  net_roi: string | null;
  /** 投产（扣除刷单）=（支付总额 − 刷单本金）÷ 推广总额。 */
  roi_ex_brushing: string | null;
}

/** 推广费用 4 卡 = 工作进度按 PR 行求和（服务费口径）；金额过 F3，收紧时为 null。 */
export interface BiPromotionSummary {
  commission_amount: string | null;
  commission_count: number;
  published_spend: string | null;
  published_count: number;
  unpublished_spend: string | null;
  unpublished_count: number;
  cancelled_amount: string | null;
  cancelled_count: number;
  publish_rate: string | null;
}

export interface BiWorkloadRow {
  pr_id: string | null;
  pr_name: string;
  target_count: number;
  quote_count: number;
  quote_progress: string | null;
  publish_count: number;
  publish_progress: string | null;
  pending_count: number;
  cancel_count: number;
  overdue_count: number;
}

/** 经营趋势一点 = 店铺日行按粒度分桶，行上是实际起止（桶 ∩ 区间）。 */
export interface BiTrendPoint {
  date_from: string;
  date_to: string;
  pay_amount: string;
  refund_amount: string;
  actual_sales: string;
  internal_spend: string;
  external_spend: string;
  total_spend: string;
  net_roi: string | null;
}

export interface BiStoreBlock {
  summary: BiStoreSummary;
  trend: BiTrendPoint[];
  promotion: BiPromotionSummary;
  workload: BiWorkloadRow[];
}

/**
 * BI 单品表一行 = 按商品合计的投产行（不拆渠道）。不带商品编码（补充 2）；
 * 站外 = 投产的推广成本，ROI = 投产的净投产（跟刷单开关）。
 */
export interface BiGoodsRow {
  goods_id: string;
  goods_title: string;
  goods_short_name: string | null;
  is_suit: boolean;
  /** 成员款号；后端图表标签同名时用它区分。 */
  style_codes: string[];
  main_image_url: string | null;
  pay_amount: string;
  refund_amount: string;
  return_rate: string | null;
  confirmed_amount: string;
  internal_spend: string;
  external_spend: string;
  total_spend: string;
  net_roi: string | null;
}

/** 柱 / 饼的一项：`goods_id` 为 null = 不可下钻（饼图的「其他」）。 */
export interface BiChartDatum {
  goods_id: string | null;
  label: string;
  value: string;
}

export interface BiGoodsBlock {
  items: BiGoodsRow[];
  /** 净投产 Top10（空 ROI 不进）。 */
  roi_top: BiChartDatum[];
  /** 支付 Top10 +「其他」（余额 > 0 才有）。 */
  pay_share: BiChartDatum[];
}

/**
 * 笔记散点一点（7e R6）= 一张已发布、录过 7 天数据的推广单：x = 发布日期，y = 7 天点赞（原始值），
 * 点大小 = 站外推广成本（F2：读不到为 null，前端用固定大小）。`detail_url` R8 之前恒 null。
 */
export interface BiNotePoint {
  promotion_id: string;
  publish_date: string;
  like_count: number | null;
  promo_cost: string | null;
  blogger_nickname: string;
  platform: string;
  detail_url: string | null;
}

/** GET /api/reports/bi/goods/{goods_id}/notes：≤ 500 点、按发布日期升序；超出取最近 500，`truncated` 为真。 */
export interface BiGoodsNotes {
  goods_id: string;
  date_from: string;
  date_to: string;
  truncated: boolean;
  points: BiNotePoint[];
}

/** GET /api/reports/bi（7e R6）：整页一次来源，店铺 / 单品两块。 */
export interface BiDashboard {
  date_from: string;
  date_to: string;
  granularity: TimeGranularity;
  exclude_brushing: boolean;
  store: BiStoreBlock;
  goods: BiGoodsBlock;
}

/**
 * 报表区间的数据新鲜度（GET /api/reports/summaries/freshness）。
 *
 * - `summary`：读的是每小时刷新的汇总表，`data_as_of` 是区间内最旧的那次刷新时间
 * - `live`：区间没有被汇总表完整覆盖，按明细实时统计，`data_as_of` 为 null
 *
 * `date_from` / `date_to` 是服务端解析预设后的具体日期 —— 预设模式下前端手里没有日期，
 * 刷新按钮要用这一对。
 */
export interface ReportFreshness {
  date_from: string;
  date_to: string;
  source: "summary" | "live";
  data_as_of: string | null;
  can_refresh: boolean;
}

/**
 * 当前用户在报表侧能看 / 能做什么（GET /api/reports/capabilities，7e R1）。
 *
 * `/me` 只给角色，前端算不了 scope：菜单与导出按钮都看它。只管显示，后端 403 才是闸门。
 * `all` / `full` = 看全部，`own` / `personal` = 只看本人，null = 无权。
 */
export interface ReportCapabilities {
  work_progress: "all" | "own" | null;
  publish_progress: "all" | "own" | null;
  bi: "full" | "personal" | null;
  production: boolean;
  /** 能否看单品投产毛利三列（7e R3：只给管理员）；false 时页面不渲染这三列。 */
  production_margin: boolean;
  store_read: boolean;
  store_write: boolean;
  /** 能导出的报表类型（`/api/reports/{type}/export` 的 type）。 */
  export: string[];
  refresh: boolean;
}

/**
 * 报表下钻的一张推广单（GET /api/reports/drill/promotions，7e R1）。
 *
 * 字段是后端白名单：不含收件信息、收款码、备注、source_extra。
 * 金额按字段权限逐行裁剪，读不到为 null（页面显示「—」）；detail_url R1 ~ R7 恒 null，不渲染链接。
 */
export interface DrillPromotionRow {
  id: string;
  internal_code: string;
  cooperation_date: string;
  scheduled_publish_date: string | null;
  actual_publish_date: string | null;
  pr_id: string | null;
  pr_name: string;
  blogger_nickname: string;
  platform: string;
  display_short_name: string;
  publish_status: string;
  recall_status: string;
  quote_amount: string | null;
  total_promo_cost: string | null;
  /** 7 天点赞，null = 未录。 */
  like_count: number | null;
  detail_url: string | null;
}

export interface DrillPromotionPage {
  items: DrillPromotionRow[];
  total: number;
  page: number;
  page_size: number;
}

/** 下钻参数：日期按合作日期、必填；pr_id 为 UUID 或 unassigned（未分配），不传 = 不限 PR。 */
export interface DrillPromotionParams {
  date_from: string;
  date_to: string;
  pr_id?: string;
  /** 归属商品 id。 */
  goods_id?: string;
  /** 季节多选（重复键）。 */
  season?: string[];
  page?: number;
  page_size?: number;
}

/** POST /api/reports/summaries/refresh 的返回：各汇总表写入行数。 */
export interface SummaryRefreshResult {
  ok: boolean;
  date_from: string;
  date_to: string;
  skipped?: string;
  [table: string]: unknown;
}
