// 阶段 / 状态 → Tag 文案与颜色（流程线设计 8.1）。颜色只做辅助，Tag 上一定有文字。
//
// PR-1 只放现有的状态（配色沿用 NegotiationPage / PromotionListPage）；用 Record<联合类型, …> 让 tsc 强制写全。
// 推广单 22 个阶段随 PR-2、谈款 7 态随 PR-3、推广单结款状态 7 个随结款的 PR 补进来。

import type { NegotiationStatus } from "@/features/negotiation/types";
import type { SettlementStatus } from "@/features/promotion/types";

export interface StageStyle {
  label: string;
  color: string;
}

export const NEGOTIATION_STATUS_STYLE: Record<NegotiationStatus, StageStyle> = {
  草稿: { label: "草稿", color: "default" },
  待审核: { label: "待审核", color: "gold" },
  审核通过: { label: "审核通过", color: "green" },
  审核驳回: { label: "审核驳回", color: "red" },
};

/** 推广单上的结款状态（settlement_status）。 */
export const PROMOTION_SETTLEMENT_STYLE: Record<SettlementStatus, StageStyle> = {
  未核查: { label: "未核查", color: "default" },
  待核查: { label: "待核查", color: "gold" },
  待付款: { label: "待付款", color: "blue" },
  已付款: { label: "已付款", color: "green" },
  已驳回: { label: "已驳回", color: "red" },
};

function lookup(table: Record<string, StageStyle>, value: string | null | undefined): StageStyle {
  if (!value) return { label: "未知", color: "default" };
  return Object.prototype.hasOwnProperty.call(table, value)
    ? table[value]
    : { label: value, color: "default" };
}

export function negotiationStatusStyle(value: string | null | undefined): StageStyle {
  return lookup(NEGOTIATION_STATUS_STYLE, value);
}

export function promotionSettlementStyle(value: string | null | undefined): StageStyle {
  return lookup(PROMOTION_SETTLEMENT_STYLE, value);
}
