// U04 promotion feature 类型定义。

import type { UiState } from "@/features/flow/keys";

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

/** 复盘状态（PRD 改动 4）。已结款 → 录 7 天数据 → 待复盘 → 待确认 → 已完成。 */
export type RetroStatus = "未开始" | "待复盘" | "待确认" | "已完成";

export type { Platform } from "@/features/common/platforms";

/** 发货 3 态（流程线 3.3）。null = 历史单，没进系统的发货流程。 */
export type ShipStatus = "待发货" | "待打单" | "已发货";

/** 推广列表「发货」筛选：none = 未进发货流程（历史单）；待发货只回阶段「待推送仓库」那批。 */
export type ShipStatusFilter = ShipStatus | "none";

/** 商品明细一行（颜色尺码，流程线 7.3）。颜色尺码实时读 SKU；界面不显示 SKU 编码（规-1）。 */
export interface PromotionItem {
  style_id: string;
  /** 款式简称，没填回落款式名。 */
  display_short_name: string;
  /** 款式全称（悬停显示）。 */
  goods_title: string;
  sku_id: string;
  color: string;
  size: string;
  style_main_image_url: string | null;
}

/** 明细应有的一个成员款式：套装 = 启用成员（按顺序），单品或没有归属 = 自身。弹窗按它每个成员出一行。 */
export interface GoodsMember {
  style_id: string;
  /** 款式简称，没填回落款式名。 */
  display_short_name: string;
  /** 款式全称（悬停显示）。 */
  goods_title: string;
}

/** 写入明细的一行：推广单入口每行都要有 sku_id，款式集合 = 归属商品的启用成员（后端校验）。 */
export interface GoodsItemIn {
  style_id: string;
  sku_id: string;
}

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
 * 字段可见性由后端字段级权限矩阵过滤，当前角色无权查看时返回 null。
 */
export interface Promotion {
  id: string;
  internal_code: string;
  style_id: string;
  sku_id: string | null;
  goods_main_id: string | null;
  blogger_id: string;
  pr_id: string | null;
  // 快照
  style_code_snapshot: string;
  /** 建单时的款式简称快照。界面的品名用 display_short_name。 */
  style_short_name_snapshot: string;
  /** 品名（7a-8）：商品简称，没填回落 style_short_name_snapshot。规则在后端一处。 */
  display_short_name: string | null;
  style_main_image_url: string | null;
  // 商品归属（实时取，不做快照 —— 归属可改）
  /** 商品编码。界面不显示（业务方 10-06），只留给搜索 / 导出。 */
  goods_code: string | null;
  goods_is_suit: boolean;
  /** 归属商品全称；没有归属商品为 null。 */
  goods_title: string | null;
  /** 归属商品简称（全空白归一成 null）。「归属商品」列显示它，没填回落 goods_title。 */
  goods_short_name: string | null;
  quote_amount: string | null; // Decimal as string；敏感
  cost_snapshot: string | null; // 敏感
  /** 寄拍 / 送拍 / 置换。历史导入数据为 null，可补一次。单据生成后不可改。 */
  cooperation_mode: string | null;
  return_shipping_fee: string | null; // 敏感
  /** 站外推广成本 = 博主服务费 + 样品成本 + 寄回运费。后端生成列，只读。敏感。 */
  total_promo_cost: string | null;
  /** 博主寄回衣服单号。寄拍模式没有它审核通不过。 */
  return_waybill: string | null;
  // 业务字段
  platform: string;
  cooperation_date: string;
  scheduled_publish_date: string | null;
  actual_publish_date: string | null;
  publish_url: string | null;
  cancel_reason: string | null;
  recall_reason: string | null;
  like_count: number | null;
  /** 发布满 7 天的收藏数 / 评论数（PRD 改动 4）。 */
  collect_count: number | null;
  comment_count: number | null;
  metrics_recorded_at: string | null;
  /** 7 天数据截图签名 URL，后端现签。 */
  metrics_signed_url: string | null;
  /**
   * 品牌词评论截图（PRD 改动 5）。没有它 publish 会 422。
   *
   * 判断「传过没有」要看 attachment_id 而不是 URL —— 签名失败时 URL 为空但图其实在。
   */
  brand_comment_attachment_id: string | null;
  brand_comment_signed_url: string | null;
  note_title: string | null;
  remark: string | null;
  // 状态
  publish_status: string;
  recall_status: string;
  settlement_status: string;
  /** 复盘状态（PRD 改动 4）。与 settlement_status 正交，互不影响。 */
  retro_status: RetroStatus;
  retro_confirmed_by: string | null;
  retro_confirmed_at: string | null;
  /** 当前生效的复盘文字 = 本单最新那条。被打回重写后旧版仍留在博主档案里。 */
  retro_content: string | null;
  // 审核
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_action: string | null;
  review_reason: string | null;
  /** 驳回原因分类：延迟发文 / 流量差补发 / 衣服未寄回。 */
  review_reason_category: string | null;
  /** 最近一次驳回后重新提交的说明（只留最近一轮）。 */
  resubmit_note: string | null;
  resubmitted_at: string | null;
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
  source_extra?: Record<string, unknown>;
  payment_qr_attachment_id: string | null;
  payment_qr_signed_url: string | null;
  settlement_payment_proof_signed_url: string | null;
  duplicate_warnings: PromotionDuplicateWarning[];
  // 收件三项（流程线 M1）：读不到的角色（财务、运营）后端给 null
  receiver_name: string | null;
  receiver_phone: string | null;
  receiver_address: string | null;
  // 发货（流程线 3.3）
  ship_status: ShipStatus | null;
  ship_pushed_at: string | null;
  ship_pushed_by_name: string | null;
  ship_courier: string | null;
  ship_waybill: string | null;
  shipped_at: string | null;
  /** 商品明细：单品 1 行、套装每个成员 1 行；没选过颜色尺码的单为空数组。 */
  items: PromotionItem[];
  /** 没有明细的旧单：「录入信息」里的颜色及规格原文；有明细时为 null。 */
  legacy_color_spec: string | null;
  /** 明细应有的成员款式（没有明细时弹窗靠它出空行）。 */
  goods_members: GoodsMember[];
  /** 流程线矩阵（7.1）：列表行 = actions + edits，详情 / 动作返回 = actions + fields。 */
  ui: UiState | null;
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
  /** 这次推广归属的商品。不传由后端取主商品（非套装优先）。 */
  goods_main_id?: string | null;
  blogger_id: string;
  /** 寄拍 / 送拍 / 置换。必填 —— 它决定成本怎么算、审核通过后走哪个出口。 */
  cooperation_mode: string;
  platform: string;
  cooperation_date: string;
  scheduled_publish_date?: string | null;
  quote_amount?: string | null;
  /** 寄回运费，一般在召回时才录。 */
  return_shipping_fee?: string | null;
  note_title?: string | null;
  remark?: string | null;
  receiver_name?: string | null;
  receiver_phone?: string | null;
  receiver_address?: string | null;
  /** 商品明细。不传 = 先不选、推送时补；传了就每行都要有 sku。 */
  items?: GoodsItemIn[] | null;
  /** 需要仓库发货（11-58）。默认 false：补录历史单不进待推送仓库。 */
  need_shipping?: boolean;
}

export interface PromotionUpdate {
  sku_id?: string | null;
  /** 商品归属可改：录错、或套装是推广录完之后才建的。 */
  goods_main_id?: string | null;
  /**
   * 只能给历史数据补一次（原值为 null 时）。已有值再传不同的值后端返回 409 —
   * 改合作模式等于改成本口径。
   */
  cooperation_mode?: string;
  /** 寄回运费，召回时录入。会自动计入站外推广成本。 */
  return_shipping_fee?: string | null;
  platform?: string;
  scheduled_publish_date?: string | null;
  quote_amount?: string | null;
  note_title?: string | null;
  like_count?: number | null;
  remark?: string | null;
  is_active?: boolean;
  /**
   * 按键合并（7a-5）：值为 null 或空串 = 删这个键，没出现的键不动。
   * 只交改过的键，见 `buildSourceExtraPatch`。
   * 「打单地址」「发货单号」已搬到 typed 列，带了后端 422 SOURCE_EXTRA_KEY_RETIRED。
   */
  source_extra?: Record<string, string | null>;
  /** 收件三项：传了才改，null 或空串 = 清空。能不能改看 `ui.edits` 是否含 receiver。 */
  receiver_name?: string | null;
  receiver_phone?: string | null;
  receiver_address?: string | null;
}

/** 确认推送仓库：弹窗里补的颜色尺码（整组替换）与收件信息（传了才改），与推送同一事务。 */
export interface PromotionShipPushRequest {
  items?: GoodsItemIn[] | null;
  receiver_name?: string | null;
  receiver_phone?: string | null;
  receiver_address?: string | null;
}

/** 撤回推送：原因必填，1 ~ 500 字。 */
export interface PromotionShipWithdrawRequest {
  reason: string;
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

/** 驳回原因分类，三选一（PRD 改动 5）。 */
export type RejectReasonCategory = "延迟发文" | "流量差补发" | "衣服未寄回";

export interface PromotionReviewRequest {
  action: ReviewAction;
  /** 驳回时必填，否则后端 422。 */
  review_reason?: string | null;
  /** 驳回时必填，三选一。审核通过时忽略。 */
  review_reason_category?: RejectReasonCategory | null;
}

/** 驳回后重新提交。note 必填；链接 / 日期不传就不改。 */
export interface PromotionResubmitRequest {
  note: string;
  publish_url?: string;
  actual_publish_date?: string;
}

/** 金额变更来源。「模式兜底」= 被按合作模式的硬规则改写了，不是人改的。 */
export type AmountChangeSource = "手动编辑" | "模式初始化" | "模式兜底";

/** 金额变更时间线的一条（PRD 第 10 节第 14 条）。 */
export interface PromotionAmountLog {
  id: string;
  field_name: "quote_amount" | "cost_snapshot" | "return_shipping_fee";
  before_value: string | null;
  after_value: string | null;
  change_source: AmountChangeSource;
  changed_by: string | null;
  changed_by_name: string | null;
  created_at: string;
}

/** 复盘记录（博主档案里的一条）。 */
export interface Retrospective {
  id: string;
  blogger_id: string;
  promotion_id: string;
  promotion_internal_code: string | null;
  style_code: string | null;
  content: string;
  created_by: string | null;
  created_by_name: string | null;
  confirmed_by: string | null;
  confirmed_by_name: string | null;
  confirmed_at: string | null;
  created_at: string;
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
  /** 发货筛选（流程线 7.3）。 */
  ship_status?: ShipStatusFilter;
  /**
   * @deprecated 后端 M1 起已删这个参数（传了被忽略）；仓库页改走 /api/warehouse 时一并删。
   */
  has_print_address?: boolean;
  /** @deprecated 同上。 */
  has_waybill?: boolean;
}
