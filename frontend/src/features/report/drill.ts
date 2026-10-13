// 报表下钻（7e R1；R5 泛化到商品 / 季节 / 周）：弹窗对象与请求参数。
import type { DrillPromotionParams } from "./types";

export const DRILL_PAGE_SIZE = 20;

/**
 * 下钻对象。日期是服务端解析后的具体区间（周行 = 行上的实际起止）。
 *
 * - `prId`：undefined = 不限 PR；null = 「未分配」行；字符串 = 该 PR
 * - `goodsId`：归属商品（单品卡片）
 * - `season`：季节多选，页面选了季节时带上，与统计口径一致
 */
export interface DrillPromotionTarget {
  /** 弹窗标题前半段：`${title} 的推广单`。 */
  title: string;
  dateFrom: string;
  dateTo: string;
  prId?: string | null;
  goodsId?: string;
  season?: string[];
}

/** 下钻请求参数：给了才带 `pr_id` / `goods_id` / `season`；「未分配」传 `pr_id=unassigned`（不传 = 所有人的单）。 */
export function drillPromotionParams(
  target: DrillPromotionTarget,
  page: number
): DrillPromotionParams {
  const params: DrillPromotionParams = {
    date_from: target.dateFrom,
    date_to: target.dateTo,
  };
  if (target.prId !== undefined) params.pr_id = target.prId ?? "unassigned";
  if (target.goodsId) params.goods_id = target.goodsId;
  if (target.season?.length) params.season = target.season;
  params.page = page;
  params.page_size = DRILL_PAGE_SIZE;
  return params;
}

/** 下钻对象的稳定键：查询键与弹窗重建都用它（换对象 → 页码回到第一页）。 */
export function drillTargetKey(target: DrillPromotionTarget): string {
  // prId 的三态要分开：undefined（不限）↔ null（未分配）
  const pr = target.prId === undefined ? "*" : (target.prId ?? "unassigned");
  return JSON.stringify([
    pr,
    target.goodsId ?? null,
    target.dateFrom,
    target.dateTo,
    target.season ?? [],
  ]);
}
