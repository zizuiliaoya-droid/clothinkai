// U04 promotion feature 类型定义。

export type PublishStatus =
  | "未发布"
  | "已发布"
  | "已取消"
  | "异常"
  | "已删除";

export type RecallStatus =
  | "未召回"
  | "召回中"
  | "召回成功"
  | "召回失败";

export type SettlementStatus =
  | "未核查"
  | "待核查"
  | "待付款"
  | "已付款"
  | "已驳回";

export type ReviewAction = "approve" | "reject";

export type Platform = "小红书" | "抖音" | "快手" | "B站";

export type UrgeStatus =
  | "已取消"
  | "已发布"
  | "已删除"
  | "未排期"
  | "档期内"
  | "催发"
  | "重要催发"
  | "超时";

/**
 * 推广响应。
 *
 * 注意：quote_amount / cost_snapshot / cpl 由后端按角色过滤；
 * 当前角色不可见时返回 null（前端在 UI 上隐藏对应列即可）。
 *
 * 衍生字段（urge_status / dual_platform / effective_like_count / is_hit / cpl）
 * 由后端实时计算，前端只读不写。
 *
 * TODO U09: 改为基于服务端字段级权限矩阵的字段过滤。
 */
export interface Promotion {
  id: string;
  internal_code: string;
  style_id: string;
  sku_id: string | null;
  blogger_id: string;
  pr_id: string | null;
  // 快照
  style_code_snapshot: string;
  style_short_name_snapshot: string;
  quote_amount: string | null; // Decimal as string；敏感
  cost_snapshot: string | null; // 敏感
  // 业务字段
  platform: string;
  cooperation_date: string;
  scheduled_publish_date: string | null;
  actual_publish_date: string | null;
  publish_url: string | null;
  cancel_reason: string | null;
  recall_reason: string | null;
  like_count: number | null;
  note_title: string | null;
  remark: string | null;
  // 状态
  publish_status: string;
  recall_status: string;
  settlement_status: string;
  // 审核
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_action: string | null;
  review_reason: string | null;
  // 通用
  is_active: boolean;
  created_at: string;
  updated_at: string;
  // 衍生字段
  urge_status: string | null;
  dual_platform: boolean;
  effective_like_count: number | null;
  is_hit: boolean;
  cpl: string | null; // 敏感
  duplicate_warnings: PromotionDuplicateWarning[];
}

export interface PromotionDuplicateWarning {
  promotion_id: string;
  internal_code: string;
  publish_status: string;
  cooperation_date: string;
}

export interface PromotionCreate {
  style_id: string;
  sku_id?: string | null;
  blogger_id: string;
  platform: string;
  cooperation_date: string;
  scheduled_publish_date?: string | null;
  quote_amount?: string | null;
  note_title?: string | null;
  remark?: string | null;
}

export interface PromotionUpdate {
  sku_id?: string | null;
  platform?: string;
  scheduled_publish_date?: string | null;
  quote_amount?: string | null;
  note_title?: string | null;
  like_count?: number | null;
  remark?: string | null;
  is_active?: boolean;
}

export interface PromotionPublishRequest {
  publish_url: string;
  actual_publish_date: string;
}

export interface PromotionCancelRequest {
  cancel_reason: string;
}

export interface PromotionRecallStartRequest {
  recall_reason?: string | null;
}

export interface PromotionReviewRequest {
  action: ReviewAction;
  review_reason?: string | null;
}

export interface PromotionPage {
  items: Promotion[];
  total: number;
  page: number;
  page_size: number;
}

export interface PromotionListFilters {
  page?: number;
  page_size?: number;
  keyword?: string;
  publish_status?: PublishStatus;
  recall_status?: RecallStatus;
  settlement_status?: SettlementStatus;
  platform?: string;
  blogger_id?: string;
  style_id?: string;
  pr_id?: string;
  cooperation_date_from?: string;
  cooperation_date_to?: string;
  scheduled_publish_date_from?: string;
  scheduled_publish_date_to?: string;
  is_active?: boolean;
  only_dual_platform?: boolean;
  is_hit?: boolean;
}
