// U05 finance feature 类型定义。

export type SettlementStatus =
  | "待核查"
  | "待付款"
  | "待财务付款"
  | "已付款"
  | "已驳回";

export type ExtraItemType = "运费" | "赞奖" | "其他";

export type ReviewAction = "approve" | "reject";

/**
 * 结算单响应。
 *
 * 注意：amount / total_amount / payment_amount / payment_proof_signed_url 由后端
 * 按角色过滤（PAYMENT_VISIBLE_ROLES：admin / pr_manager / finance）；
 * 当前角色不可见时返回 null，前端在 UI 上隐藏对应列即可。
 *
 * payment_proof_signed_url：后端生成的 R2 私有桶签名 URL（15 分钟有效）；
 * 不暴露 r2_key，前端只读不可写。
 *
 * TODO U09: 改为基于服务端字段级权限矩阵的字段过滤。
 */
export interface Settlement {
  id: string;
  settlement_no: string;
  promotion_id: string;
  blogger_id: string;
  style_id: string;
  pr_id: string | null;
  // 金额（敏感）
  amount: string | null; // Decimal as string；敏感
  total_amount: string | null; // 敏感
  payment_amount: string | null; // 敏感
  // 付款相关
  payment_date: string | null;
  payment_proof_attachment_id: string | null;
  payment_proof_signed_url: string | null; // 敏感
  // 业务字段
  note_title: string | null;
  remark: string | null;
  // 状态
  settlement_status: string;
  // 审核
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_action: string | null;
  review_reason: string | null;
  // 财务付款
  paid_by: string | null;
  // 通用
  created_at: string;
  updated_at: string;
  // 反范式展示字段（list join 填充）
  style_code: string | null;
  style_name: string | null;
  blogger_nickname: string | null;
  // 子表
  extra_items: SettlementExtraItem[];
}

export interface SettlementExtraItem {
  id: string;
  settlement_id: string;
  item_type: string;
  amount: string;
  remark: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface SettlementReviewRequest {
  action: ReviewAction;
  review_reason?: string | null;
}

export interface SettlementExtraItemCreateRequest {
  item_type: ExtraItemType;
  amount: string;
  remark?: string | null;
}

export interface SettlementPaymentAmountRequest {
  payment_amount: string;
}

export interface SettlementPaymentProofRequest {
  payment_date: string;
  payment_proof_attachment_id: string;
}

export interface SettlementPage {
  items: Settlement[];
  total: number;
  page: number;
  page_size: number;
}

export interface SettlementListFilters {
  page?: number;
  page_size?: number;
  keyword?: string;
  settlement_status?: SettlementStatus;
  promotion_id?: string;
  blogger_id?: string;
  style_id?: string;
  pr_id?: string;
  reviewed_by?: string;
  paid_by?: string;
  created_at_from?: string;
  created_at_to?: string;
  payment_date_from?: string;
  payment_date_to?: string;
  is_my?: boolean;
}

// 双口径汇总（FB7）

export interface AmountBucket {
  count: number;
  total_amount: string;
}

export interface DailySummaryAsOfResponse {
  kind: "as_of";
  date: string;
  as_of: {
    pending_review: AmountBucket;
    pending_payment: AmountBucket;
    pending_finance: AmountBucket;
    paid: AmountBucket;
    rejected: AmountBucket;
  };
  outstanding_total: AmountBucket;
}

export interface DailySummaryActivityResponse {
  kind: "activity";
  date: string;
  activity: {
    newly_created: AmountBucket;
    newly_approved: AmountBucket;
    newly_paid: AmountBucket;
    newly_rejected: AmountBucket;
  };
}

// shared attachment 基础设施（上传付款截图用）

export interface AttachmentUploadInitRequest {
  bucket: string;
  purpose: string;
  filename?: string | null;
  mime_type: string;
  size_bytes: number;
}

export interface AttachmentUploadInitResponse {
  attachment_id: string;
  presigned_url: string;
  expires_in_seconds: number;
}

export interface AttachmentResponse {
  id: string;
  bucket: string;
  purpose: string;
  filename: string | null;
  mime_type: string;
  size_bytes: number;
  status: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

// ---------------------------------------------------------------------------
// 拍单 / 刷单（order_adjustment）+ 余额核对（balance_record）— U16
// ---------------------------------------------------------------------------

export interface OrderAdjustment {
  id: string;
  order_type: string; // 拍单 / 刷单
  order_date: string | null;
  order_no: string | null;
  style_id: string | null;
  sku_id: string | null;
  style_code: string | null;
  style_name: string | null;
  blogger_identifier: string | null;
  amount: string;
  exclude_from_roi: boolean;
  status: string;
  promotion_id: string | null;
  remark: string | null;
  duplicate: boolean;
}

export interface BrushingCreate {
  order_date?: string | null;
  order_no?: string | null;
  style_id?: string | null;
  sku_id?: string | null;
  blogger_identifier?: string | null;
  amount_expr: string;
  remark?: string | null;
}

export interface BalanceRecord {
  id: string;
  record_date: string;
  record_type: string;
  income: string | null;
  expense: string | null;
  balance_after: string;
  remark: string | null;
}

export interface BalanceRecordCreate {
  record_date: string;
  record_type: string;
  income?: string | null;
  expense?: string | null;
  expected_balance?: string | null;
  remark?: string | null;
}
