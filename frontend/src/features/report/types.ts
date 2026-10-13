// report feature 类型（对齐后端 report schemas）。

export interface PrWorkProgress {
  pr_id: string | null;
  pr_name: string;
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
  /** 聚合额按报表读权限（F3），R1 恒有值；类型留 null 与后端一致。 */
  cost: string | null;
  cpl: string | null;
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

export interface StoreDailyRow {
  date: string;
  visitors: number;
  pay_amount: string;
  pay_orders: number;
  ad_spend_total: string | null;
  zhitongche_spend: string | null;
  yinli_spend: string | null;
  extra?: Record<string, unknown>;
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

export interface BiStoreSummary {
  sales_amount: string;
  refund_amount: string;
  return_rate: string | null;
  internal_spend: string;
  external_spend: string;
  total_spend: string;
  internal_spend_ratio: string | null;
  external_spend_ratio: string | null;
  roi: string | null;
}

export interface BiPromotionSummary {
  commission_amount: string;
  commission_count: number;
  published_spend: string;
  published_count: number;
  unpublished_spend: string;
  unpublished_count: number;
  cancelled_amount: string;
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

export interface BiStylePerformance {
  goods_id: string;
  goods_code: string;
  goods_title: string;
  goods_short_name: string | null;
  is_suit: boolean;
  /** 成员款号（补充 2 起返回）；后端图表标签同名时用它区分。 */
  style_codes: string[];
  main_image_url: string | null;
  sales_amount: string;
  refund_amount: string;
  return_rate: string | null;
  confirmed_amount: string;
  internal_spend: string;
  external_spend: string;
  total_spend: string;
  roi: string | null;
}

export interface BiTrendPoint {
  date: string;
  sales_amount: string;
  refund_amount: string;
  internal_spend: string;
  external_spend: string;
}

export interface BiDashboard {
  date_from: string;
  date_to: string;
  granularity: TimeGranularity;
  store_summary: BiStoreSummary;
  promotion_summary: BiPromotionSummary;
  workload: BiWorkloadRow[];
  style_performance: BiStylePerformance[];
  trend: BiTrendPoint[];
  cards: Array<Record<string, unknown>>;
  charts: Array<Record<string, unknown>>;
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

/** 下钻参数：日期按合作日期、必填；pr_id 为 UUID 或 unassigned（未分配）。 */
export interface DrillPromotionParams {
  date_from: string;
  date_to: string;
  pr_id?: string;
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
