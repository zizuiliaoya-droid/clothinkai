import { UNASSIGNED_PR, type ReportFilterSchema } from "@/features/report/reportFilters";
import type { PromotionListFilters } from "./types";

export interface PromotionDimensions {
  goods_id?: string;
  season: string[];
  pr_id?: string;
}

export const PROMOTION_FILTER_SCHEMA: ReportFilterSchema<PromotionDimensions> = {
  goods_id: { kind: "uuid", url: true, memory: true, landing: true },
  season: { kind: "string_list", url: true, memory: true },
  pr_id: { kind: "pr_id", url: true, memory: true, landing: true },
};
export const PROMOTION_FILTER_DEFAULTS: PromotionDimensions = { season: [] };

export function promotionDimensionParams(value: PromotionDimensions): PromotionListFilters {
  return {
    goods_main_id: value.goods_id,
    seasons: value.season.length ? value.season : undefined,
    pr_id: value.pr_id === UNASSIGNED_PR ? undefined : value.pr_id,
    unassigned_only: value.pr_id === UNASSIGNED_PR,
  };
}

export function promotionIdFromUrl(raw: string | null): string | undefined {
  return raw && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw)
    ? raw.toLowerCase()
    : undefined;
}
