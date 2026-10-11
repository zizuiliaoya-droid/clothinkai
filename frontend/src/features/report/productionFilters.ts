// 投产页筛选（product_roi）的 schema 与记忆回填 / 保存（8a-3，设计 §8.4；7e R1 并入 useReportFilters）。
import { TIME_PRESETS, pickMemoryFilters, restoreMemoryFilters, type ReportFilterSchema } from "./reportFilters";
import type { TimePreset } from "./types";

/** 投产页筛选状态。日期区间只从 URL 带入、不记（每次进来通常想看最新）；类目已下线不再记。 */
export interface ProductionFilters {
  preset: TimePreset;
  date_from?: string;
  date_to?: string;
  season: string[];
  exclude_brushing: boolean;
}

/** 记忆到 user_preference 的筛选形态。 */
export type ProductionFilterMemory = Pick<ProductionFilters, "preset" | "season" | "exclude_brushing">;

export const PRODUCTION_FILTER_SCHEMA: ReportFilterSchema<ProductionFilters> = {
  preset: { kind: "enum", values: TIME_PRESETS, url: true, memory: true },
  date_from: { kind: "date", url: true, memory: false },
  date_to: { kind: "date", url: true, memory: false },
  season: { kind: "string_list", url: true, memory: true },
  exclude_brushing: { kind: "boolean", url: true, memory: true },
};

export const PRODUCTION_FILTER_DEFAULTS: ProductionFilters = {
  preset: "last_30d",
  season: [],
  exclude_brushing: true,
};

/**
 * 从存量记忆里取能用的筛选项：只认 preset / season / exclude_brushing。
 *
 * 旧记录里的 category（类目已下线）与其他未知键一律忽略；类型不对的值丢弃，
 * 由页面保持自己的默认值——记忆是便利，坏数据不该让页面出错或按错误条件过滤。
 */
export function restoreProductionFilters(saved: unknown): Partial<ProductionFilterMemory> {
  return restoreMemoryFilters(saved, PRODUCTION_FILTER_SCHEMA);
}

/** 页面状态 → 要保存的记忆：只写 preset / season / exclude_brushing。 */
export function toProductionMemory(state: ProductionFilterMemory): ProductionFilterMemory {
  return pickMemoryFilters<ProductionFilters>(state, PRODUCTION_FILTER_SCHEMA) as ProductionFilterMemory;
}
