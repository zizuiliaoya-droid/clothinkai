import type { PromotionStage } from "@/features/flow/stageStyle";
import type { PromotionBadge, PromotionListFilters, PromotionStageCounts } from "./types";

export const BADGE_LABELS: Record<PromotionBadge, string> = {
  missing_qr: "缺收款码", metrics_due: "满 7 天 · 可录", metrics_wait_settle: "满 7 天 · 待结款",
  retro_due: "复盘待写", freight_open: "运费单未结",
};
type QuickFilter = { label: string } & ({ stage: PromotionStage } | { badge: PromotionBadge } | { completed: true });
export const QUICK_FILTERS: readonly QuickFilter[] = [
  ...(["待推送仓库", "待仓库发货", "催发", "重要催发", "超时", "待主管审核"] as const).map((stage) => ({ label: stage, stage })),
  { label: "缺收款码", badge: "missing_qr" },
  ...(["推广驳回 · 待重提", "待财务付款", "结款驳回 · 待重提", "待传结款截图"] as const).map((stage) => ({ label: stage, stage })),
  { label: "满 7 天可录", badge: "metrics_due" }, { label: "复盘待写", badge: "retro_due" }, { label: "已完结", completed: true },
];
export function quickSelected(filters: PromotionListFilters, item: QuickFilter): boolean {
  if ("stage" in item) return filters.stage === item.stage && !filters.badge && filters.completed !== true;
  if ("badge" in item) return filters.badge === item.badge && !filters.stage && filters.completed !== true;
  return filters.completed === true && !filters.stage && !filters.badge;
}
export function quickCount(counts: PromotionStageCounts | undefined, item: QuickFilter): number | undefined {
  if (!counts) return undefined;
  return "stage" in item ? counts.stages[item.stage] : "badge" in item ? counts.badges[item.badge] : counts.completed;
}
export function toggleQuickFilter(filters: PromotionListFilters, item: QuickFilter): Partial<PromotionListFilters> {
  const reset = { stage: undefined, badge: undefined, completed: filters.completed === false ? false : undefined, page: 1 };
  if (quickSelected(filters, item)) return reset;
  return { ...reset, ...("stage" in item ? { stage: item.stage } : "badge" in item ? { badge: item.badge } : { completed: true }) };
}
/** 计数与列表共用其他条件；不带分页，也不随当前stage/badge重复请求。 */
export function stageCountParams(filters: PromotionListFilters): PromotionListFilters {
  const rest = { ...filters };
  delete rest.stage;
  delete rest.badge;
  delete rest.page;
  delete rest.page_size;
  return rest;
}
