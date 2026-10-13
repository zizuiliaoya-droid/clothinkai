// 工作进度页筛选（7e R1 区间；R5 加季节）：URL → 记忆 → 默认，见 useReportFilters。
import { TIME_PRESETS, type ReportFilterSchema } from "../reportFilters";
import type { TimePreset } from "../types";

/**
 * 区间 = preset + 起止日期，记忆里连日期一起记；旧记忆 `{month}` 不再认，回落默认本月。
 *
 * 季节存进 `pr_work_progress` 的筛选记忆就是「锁定」：选 / 清季节即写记忆（清空写 `[]` = 解锁）；
 * URL 带来的季节只本次生效、不写回。卡片、PR 表、周表、导出、下钻带同一个季节。
 */
export interface WorkProgressFilters {
  preset: TimePreset;
  date_from?: string;
  date_to?: string;
  season: string[];
}

export const WORK_PROGRESS_FILTER_SCHEMA: ReportFilterSchema<WorkProgressFilters> = {
  preset: { kind: "enum", values: TIME_PRESETS, url: true, memory: true },
  date_from: { kind: "date", url: true, memory: true },
  date_to: { kind: "date", url: true, memory: true },
  season: { kind: "string_list", url: true, memory: true },
};

export const WORK_PROGRESS_FILTER_DEFAULTS: WorkProgressFilters = {
  preset: "this_month",
  season: [],
};

/** 请求参数里的季节：没选不传（= 不限季节）。 */
export function seasonParam(season: string[]): string[] | undefined {
  return season.length ? season : undefined;
}
