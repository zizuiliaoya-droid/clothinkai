// BI 下钻（7e R6，设计 2.6）：ROI 柱 / 饼 / 单品行 → 单品投产，PR 工作量行 → 工作进度。
// 链接带 preset=custom + 响应上的实际起止；投产再带刷单开关。BI 没有季节筛选，不带 season（偏差 9）。
// 能不能点看 capabilities：只管显示，后端 403 才是闸门。
import { UNASSIGNED_PR } from "../reportFilters";
import type { ReportCapabilities } from "../types";

/** BI 响应上的区间与刷单开关（下钻用响应的值，与页面上看到的数字一致）。 */
export interface BiDrillRange {
  date_from: string;
  date_to: string;
  exclude_brushing: boolean;
}

export const NEED_PRODUCTION_HINT = "需要投产报表权限";
export const NEED_WORK_PROGRESS_HINT = "需要工作进度权限";

function rangeParams(range: BiDrillRange): URLSearchParams {
  return new URLSearchParams({
    preset: "custom",
    date_from: range.date_from,
    date_to: range.date_to,
  });
}

/** 单品投产落点：落点页数据到后对该商品开一次趋势。 */
export function productionDrillHref(range: BiDrillRange, goodsId: string): string {
  const params = rangeParams(range);
  params.set("exclude_brushing", String(range.exclude_brushing));
  params.set("goods_id", goodsId);
  return `/production?${params.toString()}`;
}

/** 工作进度落点：`prId` 为 null =「未分配」行，传 `pr_id=unassigned`。 */
export function workProgressDrillHref(range: BiDrillRange, prId: string | null): string {
  const params = rangeParams(range);
  params.set("pr_id", prId ?? UNASSIGNED_PR);
  return `/work-progress?${params.toString()}`;
}

/** ROI 柱 / 饼 / 单品行能否点：要有投产读权限；能力没回来前一律不可点。 */
export function canDrillProduction(caps: ReportCapabilities | undefined): boolean {
  return caps?.production === true;
}

/** PR 工作量行能否点：工作进度能力非空（全部或只看本人）。 */
export function canDrillWorkProgress(caps: ReportCapabilities | undefined): boolean {
  return caps?.work_progress != null;
}
