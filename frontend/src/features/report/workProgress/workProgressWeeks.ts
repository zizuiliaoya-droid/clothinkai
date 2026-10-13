// 工作进度周明细（7e R5）：周表的查询、周期标签与「周行 → 下钻对象」。
import { useQuery } from "@tanstack/react-query";
import { isForbiddenError } from "@/features/warehouse/shipmentForm";
import { getWorkProgressWeeks, type WorkProgressParams } from "../api";
import type { DrillPromotionTarget } from "../drill";
import { storePeriodLabel } from "../store/storeColumns";
import type { WorkProgressWeekRow } from "../types";
/** 周明细查询；页面拿它的 error 一并判 403。 */
export function useWorkProgressWeeks(params: WorkProgressParams, enabled: boolean) {
  return useQuery({
    queryKey: [
      "work-progress-weeks",
      params.preset,
      params.date_from,
      params.date_to,
      params.season ?? [],
    ],
    enabled,
    // 403 重试没有意义，页面直接显示无权限
    retry: (count, err) => !isForbiddenError(err) && count < 1,
    queryFn: () => getWorkProgressWeeks(params),
  });
}
/** 周期标签：行上实际起止（周 ∩ 区间，首尾周被截断），同店铺周表「MM-DD ~ MM-DD」、跨年带年份。 */
export function weekPeriodLabel(row: WorkProgressWeekRow): string {
  return storePeriodLabel(row, "week");
}
/** 周行 → 下钻对象：行上实际起止 + 当前季节，不限 PR / 商品。 */
export function weekDrillTarget(
  row: WorkProgressWeekRow,
  season: string[] | undefined
): DrillPromotionTarget {
  return {
    title: weekPeriodLabel(row),
    dateFrom: row.date_from,
    dateTo: row.date_to,
    season,
  };
}
