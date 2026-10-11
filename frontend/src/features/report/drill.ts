// 报表下钻（7e R1）：弹窗对象与请求参数。
import type { DrillPromotionParams } from "./types";

export const DRILL_PAGE_SIZE = 20;

/** 弹窗打开的对象：`prId` 为 null = 「未分配」行。日期是服务端解析后的具体区间。 */
export interface DrillPromotionTarget {
  prId: string | null;
  prName: string;
  dateFrom: string;
  dateTo: string;
}

/** 下钻请求参数：「未分配」行传 `pr_id=unassigned`（不传 = 不限 PR，会把所有人的单都列出来）。 */
export function drillPromotionParams(
  target: DrillPromotionTarget,
  page: number
): DrillPromotionParams {
  return {
    date_from: target.dateFrom,
    date_to: target.dateTo,
    pr_id: target.prId ?? "unassigned",
    page,
    page_size: DRILL_PAGE_SIZE,
  };
}