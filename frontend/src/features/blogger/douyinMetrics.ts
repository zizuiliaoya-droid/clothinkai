// 8b §6.3 / §7.4：灰豚抖音统计列。列名清单读后端测试比对过的快照（backend tests/unit/test_douyin_constants.py），
// 不在前端另写一份，两边逐字一致。
import snapshot from "./douyinMetrics.snapshot.json";
import type { BloggerPlatformMetrics } from "./types";

/** 博主页平台筛「抖音」时展示的指标列（23 列，重名列带「分组·」前缀）。 */
export const DOUYIN_METRIC_FIELDS: readonly string[] = snapshot.columns;

/** 平台筛「抖音」时指标列换成抖音的，否则照旧用小红书灰豚的 crawler_metrics。 */
export function usesDouyinMetrics(platform?: string | null): boolean {
  return platform === "抖音";
}

/** 展示原文（raw，如「12.3w」「女性居多，占比84.06%」），不展示解析值；没有就显示 —。 */
export function douyinMetricText(
  metrics: BloggerPlatformMetrics | null | undefined,
  field: string
): string {
  const v = metrics?.raw?.[field];
  return v == null || v.trim() === "" ? "—" : v;
}
