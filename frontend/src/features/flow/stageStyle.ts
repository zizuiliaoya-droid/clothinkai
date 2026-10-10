// 阶段 / 状态 → Tag 文案与颜色（流程线设计 8.1）。颜色只做辅助，Tag 上一定有文字。
//
// 用 Record<联合类型, …> 让 tsc 强制写全。推广单 22 个阶段与发货 3 态随 PR-2；
// 谈款 7 态随 PR-3、推广单结款状态 7 个随结款的 PR 补进来（现在是旧值，配色沿用 NegotiationPage / PromotionListPage）。

import type { NegotiationStatus } from "@/features/negotiation/types";
import type { SettlementStatus, ShipStatus } from "@/features/promotion/types";

export interface StageStyle {
  label: string;
  color: string;
}

/**
 * 推广单派生阶段（3.8），照抄后端 promotion/stage_calculator.py 的 PROMOTION_STAGES，顺序 = A ~ H 列。
 * 与后端的对齐由 keys.snapshot.test.ts 读后端导出的 flow_keys.snapshot.json 守。
 */
export const PROMOTION_STAGES = [
  "待推送仓库",
  "待仓库发货",
  "档期内",
  "催发",
  "重要催发",
  "超时",
  "未排期",
  "待主管审核",
  "推广驳回 · 待重提",
  "待财务付款",
  "结款驳回 · 待重提",
  "待传结款截图",
  "待满 7 天",
  "待录 7 天数据",
  "待复盘",
  "召回中",
  "已完结",
  "已完结 · 召回",
  "已完结 · 不合作",
  "已停用",
  "已删除",
  "状态异常",
] as const;
export type PromotionStage = (typeof PROMOTION_STAGES)[number];

/** 矩阵列（5.3 的 A ~ H，= 接口 ui.column）。 */
export type FlowColumn = "A" | "B" | "C" | "D" | "E" | "F" | "G" | "H";

/** 阶段 → 矩阵列，照抄后端 STAGE_COLUMN。 */
export const PROMOTION_STAGE_COLUMN: Record<PromotionStage, FlowColumn> = {
  待推送仓库: "A",
  待仓库发货: "B",
  档期内: "C",
  催发: "C",
  重要催发: "C",
  超时: "C",
  未排期: "C",
  待主管审核: "D",
  "推广驳回 · 待重提": "D",
  待财务付款: "E",
  "结款驳回 · 待重提": "E",
  待传结款截图: "E",
  "待满 7 天": "F",
  "待录 7 天数据": "F",
  待复盘: "F",
  召回中: "G",
  已完结: "H",
  "已完结 · 召回": "H",
  "已完结 · 不合作": "H",
  已停用: "H",
  已删除: "H",
  状态异常: "H",
};

/**
 * 推广单阶段 → Tag。文案 = 阶段名（与后端同名，界面不另起文案）。
 * 配色：催发 3 档沿用推广列表「是否催发」列；等人处理的偏暖、驳回偏橙红、完结绿、停用 / 未排期不上色。
 */
export const PROMOTION_STAGE_STYLE: Record<PromotionStage, StageStyle> = {
  待推送仓库: { label: "待推送仓库", color: "gold" },
  待仓库发货: { label: "待仓库发货", color: "blue" },
  档期内: { label: "档期内", color: "default" },
  催发: { label: "催发", color: "orange" },
  重要催发: { label: "重要催发", color: "red" },
  超时: { label: "超时", color: "red" },
  未排期: { label: "未排期", color: "default" },
  待主管审核: { label: "待主管审核", color: "gold" },
  "推广驳回 · 待重提": { label: "推广驳回 · 待重提", color: "volcano" },
  待财务付款: { label: "待财务付款", color: "blue" },
  "结款驳回 · 待重提": { label: "结款驳回 · 待重提", color: "volcano" },
  待传结款截图: { label: "待传结款截图", color: "cyan" },
  "待满 7 天": { label: "待满 7 天", color: "default" },
  "待录 7 天数据": { label: "待录 7 天数据", color: "geekblue" },
  待复盘: { label: "待复盘", color: "purple" },
  召回中: { label: "召回中", color: "orange" },
  已完结: { label: "已完结", color: "green" },
  "已完结 · 召回": { label: "已完结 · 召回", color: "green" },
  "已完结 · 不合作": { label: "已完结 · 不合作", color: "default" },
  已停用: { label: "已停用", color: "default" },
  已删除: { label: "已删除", color: "red" },
  状态异常: { label: "状态异常", color: "magenta" },
};

/** 推广单发货 3 态（3.3）。null（历史单）不走这里，列表显示「—」。 */
export const SHIP_STATUS_STYLE: Record<ShipStatus, StageStyle> = {
  待发货: { label: "待发货", color: "gold" },
  待打单: { label: "待打单", color: "blue" },
  已发货: { label: "已发货", color: "green" },
};

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

export function promotionStageStyle(value: string | null | undefined): StageStyle {
  return lookup(PROMOTION_STAGE_STYLE, value);
}

export function shipStatusStyle(value: string | null | undefined): StageStyle {
  return lookup(SHIP_STATUS_STYLE, value);
}
