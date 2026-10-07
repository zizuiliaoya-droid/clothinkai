// 投产页筛选记忆（product_roi）的回填与保存，抽成纯函数便于测试（8a-3，设计 §8.4）。
import type { TimePreset } from "./types";

/** 记忆到 user_preference 的筛选形态。日期区间不记（每次进来通常想看最新）；类目已下线不再记。 */
export interface ProductionFilterMemory {
  preset: TimePreset;
  season: string[];
  exclude_brushing: boolean;
}

const TIME_PRESETS: readonly TimePreset[] = [
  "last_7d",
  "last_30d",
  "this_month",
  "last_month",
  "custom",
];

function isTimePreset(v: unknown): v is TimePreset {
  return typeof v === "string" && (TIME_PRESETS as readonly string[]).includes(v);
}

/**
 * 从存量记忆里取能用的筛选项：只认 preset / season / exclude_brushing。
 *
 * 旧记录里的 category（类目已下线）与其他未知键一律忽略；类型不对的值丢弃，
 * 由页面保持自己的默认值——记忆是便利，坏数据不该让页面出错或按错误条件过滤。
 */
export function restoreProductionFilters(saved: unknown): Partial<ProductionFilterMemory> {
  if (saved === null || typeof saved !== "object" || Array.isArray(saved)) return {};
  const raw = saved as Record<string, unknown>;
  const out: Partial<ProductionFilterMemory> = {};
  if (isTimePreset(raw.preset)) out.preset = raw.preset;
  if (Array.isArray(raw.season) && raw.season.every((s) => typeof s === "string")) {
    out.season = raw.season as string[];
  }
  if (typeof raw.exclude_brushing === "boolean") out.exclude_brushing = raw.exclude_brushing;
  return out;
}

/** 页面状态 → 要保存的记忆：只写 preset / season / exclude_brushing。 */
export function toProductionMemory(state: ProductionFilterMemory): ProductionFilterMemory {
  return {
    preset: state.preset,
    season: state.season,
    exclude_brushing: state.exclude_brushing,
  };
}
