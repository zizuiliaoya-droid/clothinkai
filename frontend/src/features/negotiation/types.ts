// 谈款审核（PRD V1.4 模块一）类型。

export type NegotiationStatus = "草稿" | "待审核" | "审核通过" | "审核驳回";
export type CooperationMode = "寄拍" | "送拍" | "置换";

export interface Negotiation {
  id: string;
  blogger_id: string;
  blogger_nickname: string | null;
  style_id: string;
  style_code: string | null;
  style_name: string | null;
  goods_main_id: string | null;
  goods_code: string | null;
  goods_title: string | null;
  goods_is_suit: boolean;

  pr_id: string;
  pr_name: string | null;
  /** 审核通过后生成的推广单，据此跳转到推广管理。 */
  promotion_id: string | null;
  promotion_internal_code: string | null;

  cooperation_mode: CooperationMode;
  platform: string;
  scheduled_publish_date: string | null;
  /** 敏感字段，无报价读权限时为 null。 */
  quote_amount: string | null;
  remark: string | null;

  status: NegotiationStatus;
  submitted_at: string | null;
  reviewed_by: string | null;
  reviewer_name: string | null;
  reviewed_at: string | null;
  review_opinion: string | null;

  created_at: string;
  updated_at: string;
}

export interface NegotiationPage {
  items: Negotiation[];
  total: number;
  page: number;
  page_size: number;
}

export interface NegotiationFilters {
  status?: NegotiationStatus;
  blogger_id?: string;
  style_id?: string;
  pr_id?: string;
  cooperation_mode?: CooperationMode;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export interface NegotiationCreate {
  blogger_id: string;
  style_id: string;
  goods_main_id?: string | null;
  cooperation_mode: CooperationMode;
  platform?: string;
  scheduled_publish_date?: string | null;
  /** 置换模式填了也会被后端置 0。 */
  quote_amount?: string | null;
  remark?: string | null;
}

export interface NegotiationUpdate {
  blogger_id?: string;
  style_id?: string;
  goods_main_id?: string | null;
  cooperation_mode?: CooperationMode;
  platform?: string;
  scheduled_publish_date?: string | null;
  quote_amount?: string | null;
  remark?: string | null;
}

export interface NegotiationReviewRequest {
  action: "approve" | "reject";
  /** 驳回时必填。 */
  review_opinion?: string | null;
}

/**
 * 博主历史合作的一条记录（hover 卡用）。
 *
 * PRD 还要求「当时 ROI」，但博主维度 ROI 系统里没定义过，还要先定「发布后多少天」
 * 这个窗口。所以先给已有口径的 CPL 与点赞数。
 */
export interface BloggerCooperationItem {
  promotion_id: string;
  internal_code: string;
  style_id: string;
  style_code: string;
  style_name: string | null;
  style_main_image_url: string | null;
  cooperation_date: string;
  cooperation_mode: string | null;
  publish_status: string;
  actual_publish_date: string | null;
  like_count: number | null;
  /** 单赞成本。敏感，无报价读权限时为 null。 */
  cpl: string | null;
  quote_amount: string | null;
}

export interface BloggerCooperationHistory {
  blogger_id: string;
  /** 历史合作总数，不受 limit 影响。 */
  total_cooperations: number;
  items: BloggerCooperationItem[];
}
