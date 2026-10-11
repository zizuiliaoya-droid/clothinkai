// 区间的数据新鲜度查询（含服务端解析后的具体起止日期）。
import { useQuery } from "@tanstack/react-query";
import { getSummaryFreshness } from "./api";
import type { TimePreset } from "./types";

/**
 * 与 `<ReportFreshness>` 共用同一个查询键：页面要具体日期（如下钻）时调它，不会多发请求。
 * 预设模式（本月、近 30 天等）下前端手里没有日期，用这里的 `date_from` / `date_to`。
 */
export function useSummaryFreshness(
  preset: TimePreset,
  dateFrom: string | undefined,
  dateTo: string | undefined,
  enabled = true
) {
  return useQuery({
    queryKey: ["report-freshness", preset, dateFrom, dateTo],
    enabled,
    queryFn: () => getSummaryFreshness({ preset, date_from: dateFrom, date_to: dateTo }),
  });
}