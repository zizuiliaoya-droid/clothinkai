// report feature 类型（对齐后端 report schemas）。

export interface PrWorkProgress {
  pr_id: string | null;
  pr_name: string;
  quote_count: number;
  in_schedule_count: number;
  urge_count: number;
  important_urge_count: number;
  overdue_count: number;
  publish_count: number;
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
  cost: string;
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
}

export interface ProductionRow {
  style_id: string;
  style_code: string;
  style_name: string;
  pay_amount: string;
  refund_amount: string;
  return_rate: string | null;
  confirmed_amount: string;
  promo_cost: string;
  ad_spend: string;
  total_spend: string;
  add_cart_count: number;
  add_cart_cost: string | null;
  net_roi: string | null;
  unit_deal_cost: string | null;
}

export interface ProductionReport {
  items: ProductionRow[];
  previous: ProductionRow[] | null;
}

export type TimePreset =
  | "last_7d"
  | "last_30d"
  | "this_month"
  | "last_month"
  | "custom";
