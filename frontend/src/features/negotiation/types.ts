// 谈款审核类型（PRD V1.4 模块一；流程线 PR-3 起 7 态两级审核，设计 3.2、7.2）。

import type { UiState } from "@/features/flow/keys";
import type { GoodsItemIn, GoodsMember } from "@/features/promotion/types";

/** 谈款 7 态的存储值（3.2），顺序 = 谈款页 Tab 顺序。界面文案见 stageStyle.ts。 */
export const NEGOTIATION_STATUSES = [
  "主管待审",
  "待老板审核",
  "老板退回",
  "待补收货地址",
  "草稿",
  "审核驳回",
  "已定稿",
] as const;
export type NegotiationStatus = (typeof NEGOTIATION_STATUSES)[number];
export type CooperationMode = "寄拍" | "送拍" | "置换";

/** 谈款商品明细一行（7.2）。颜色尺码实时读 SKU；`sku_id` 为空 = 还没选。界面不显示编码（规-1）。 */
export interface NegotiationItem {
  id: string;
  style_id: string;
  /** 搜索、导出用，界面不显示。 */
  style_code: string;
  /** 款式简称，没填回落款式名。 */
  display_short_name: string;
  /** 款式全称（悬停显示）。 */
  goods_title: string;
  sku_id: string | null;
  color: string | null;
  size: string | null;
  style_main_image_url: string | null;
}

/** 写入谈款明细的一行：sku 可空（保存草稿不卡，提交时才要求每行都选）。 */
export interface NegotiationItemIn {
  style_id: GoodsItemIn["style_id"];
  sku_id: string | null;
}

/** 同一博主有别的 PR 在谈（7c-16）：只给同事姓名、状态、更新时间。 */
export interface OthersInProgressItem {
  pr_name: string;
  status: string;
  updated_at: string;
}

/** 待补收货地址时的预填（3.2）：只做预填，PR 要点确认。 */
export interface ReceiverSuggestion {
  receiver_name: string | null;
  receiver_phone: string | null;
  receiver_address: string | null;
  source: "promotion" | "negotiation";
  /** 如「推广单 DE2610030A1 · 10-03」「谈款定稿 · 10-02」。 */
  source_label: string;
}

export interface Negotiation {
  id: string;
  /** 主线号（开抽屉、读时间线用）。 */
  line_id: string;
  /** 「复制为新谈款」的来源。 */
  copied_from_id: string | null;
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
  /** 定稿后生成的推广单，据此跳转到推广管理。 */
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
  /** 第一级（主管）。 */
  reviewed_by: string | null;
  reviewer_name: string | null;
  reviewed_at: string | null;
  review_opinion: string | null;
  /** 第二级（老板 = 管理员）。 */
  final_reviewed_by: string | null;
  final_reviewer_name: string | null;
  final_reviewed_at: string | null;
  final_review_opinion: string | null;

  /** 收件三项按字段规则投影，财务、运营拿到 null。 */
  receiver_name: string | null;
  receiver_phone: string | null;
  receiver_address: string | null;
  receiver_confirmed_at: string | null;
  receiver_confirmed_by: string | null;

  /** 品名：商品简称 → 款式简称 → 款式名。 */
  display_short_name: string | null;
  items: NegotiationItem[];
  /** 主管待审超 36 小时；看不到超时高亮的人恒为 false。 */
  overdue: boolean;
  overdue_hours: number | null;
  /** 只在详情、待补收货地址、当前用户能录收货信息时有。 */
  receiver_suggestion: ReceiverSuggestion | null;
  /** 只在新建与提交的响应里有；其余接口为 null。 */
  others_in_progress: OthersInProgressItem[] | null;
  /** 矩阵 ui：列表行 actions + edits，详情 actions + fields。 */
  ui: UiState | null;

  created_at: string;
  updated_at: string;
}

export interface NegotiationPage {
  items: Negotiation[];
  total: number;
  page: number;
  page_size: number;
  /** 页级 ui.actions.create（新建谈款）。 */
  ui: UiState;
}

export interface NegotiationFilters {
  status?: NegotiationStatus;
  blogger_id?: string;
  style_id?: string;
  pr_id?: string;
  cooperation_mode?: CooperationMode;
  keyword?: string;
  /** 只看主管待审超 36 小时的（「超时」角标）。 */
  overdue_only?: boolean;
  page?: number;
  page_size?: number;
}

export interface NegotiationCreate {
  blogger_id: string;
  style_id: string;
  /** 不传由后端按款式推定主商品并写回。 */
  goods_main_id?: string | null;
  cooperation_mode: CooperationMode;
  platform?: string;
  scheduled_publish_date?: string | null;
  /** 不传取博主档案报价；置换模式后端强制 0。 */
  quote_amount?: string | null;
  remark?: string | null;
  /** 不传按商品成员建空行；传了款式集合要与成员相同。 */
  items?: NegotiationItemIn[] | null;
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
  /** 传了整组替换。 */
  items?: NegotiationItemIn[] | null;
}

/** 主管审核（N4 / N5）。 */
export interface NegotiationReviewRequest {
  action: "approve" | "reject";
  /** 驳回时必填。 */
  review_opinion?: string | null;
}

/** 老板审核（N6 定稿 / N7 退回）。 */
export interface NegotiationFinalReviewRequest {
  action: "approve" | "return";
  /** 退回时必填。 */
  opinion?: string | null;
}

/** 保存收货信息（N8）：只改传了的项，空串 / null = 清空。 */
export interface NegotiationReceiverUpdate {
  receiver_name?: string | null;
  receiver_phone?: string | null;
  receiver_address?: string | null;
}

/** 确认收货信息并生成推广单（N9）：以 body 为准。 */
export interface NegotiationReceiverConfirm {
  receiver_name: string | null;
  receiver_phone: string | null;
  receiver_address: string | null;
}

/** `GET /goods-members`：明细该有哪几行；goods_main_id = 传入的或后端推定的。 */
export interface NegotiationGoodsMembers {
  goods_main_id: string | null;
  members: GoodsMember[];
}

/**
 * 博主历史合作的一条记录（hover 卡用）。
 *
 * PRD 原文有「当时 ROI」，业务方 10-02 确认卡片上不算 ROI，只看单篇点赞成本。
 */
export interface BloggerCooperationItem {
  promotion_id: string;
  internal_code: string;
  style_id: string;
  style_code: string;
  /** 建单时的款式简称快照。界面显示 display_short_name。 */
  style_name: string | null;
  /** 品名（7a-8）：商品简称，没填回落快照。 */
  display_short_name: string | null;
  /** 归属商品全称（悬停提示）；没有归属商品为 null。 */
  goods_title: string | null;
  style_main_image_url: string | null;
  cooperation_date: string;
  cooperation_mode: string | null;
  publish_status: string;
  actual_publish_date: string | null;
  like_count: number | null;
  /**
   * 单篇点赞成本 = 总推广成本 ÷ 折算后 7 天点赞数，录过 7 天数据才有值。
   * 敏感：要同时能看报价与成本，否则为 null。
   */
  cpl: string | null;
  quote_amount: string | null;
}

/** 博主卡片「谈款驳回记录」一条（7c-7）：所有 PR 的主管驳回单。 */
export interface NegotiationRejectionItem {
  /** 只给看得到这张谈款的人，其余为 null（不跳转）。 */
  negotiation_id: string | null;
  pr_name: string | null;
  rejected_at: string;
  /** 搜索用，界面不显示。 */
  style_code: string | null;
  display_short_name: string | null;
  platform: string | null;
  /** 按报价字段规则裁剪（运营为 null）。 */
  quote_amount: string | null;
  opinion: string | null;
}

export interface BloggerCooperationHistory {
  blogger_id: string;
  /** 历史合作总数，不受 limit 影响。 */
  total_cooperations: number;
  items: BloggerCooperationItem[];
  /** 最近 5 条主管驳回，按驳回时间倒序。 */
  rejections: NegotiationRejectionItem[];
  /** 主管驳回总数（不受 5 条限制）。 */
  total_rejections: number;
}
