// 工作进度单品（7e R5）：卡片那一页的查询与「商品行 → 下钻对象」，卡片与「查看全部」弹窗共用。
import { useQuery } from "@tanstack/react-query";
import { isForbiddenError } from "@/features/warehouse/shipmentForm";
import { getWorkProgressGoods, type WorkProgressParams } from "../api";
import type { DrillPromotionTarget } from "../drill";
import type { WorkProgressGoodsPage, WorkProgressGoodsRow } from "../types";

/** 卡片数（设计 2.5：前 12 张卡）。 */
export const GOODS_CARD_LIMIT = 12;

/** 「查看全部」弹窗每页条数；「未归属商品」接在最后一页末尾、不占条数。 */
export const GOODS_MODAL_PAGE_SIZE = 20;

/** 单品接口的查询键：区间 + 季节 + 分页。 */
export function workProgressGoodsKey(params: WorkProgressParams, limit: number, offset: number) {
  return [
    "work-progress-goods",
    params.preset,
    params.date_from,
    params.date_to,
    params.season ?? [],
    limit,
    offset,
  ];
}

/** 卡片那一页（limit 12、offset 0）；页面拿它的 error 一并判 403。 */
export function useWorkProgressGoodsCards(params: WorkProgressParams, enabled: boolean) {
  return useQuery({
    queryKey: workProgressGoodsKey(params, GOODS_CARD_LIMIT, 0),
    enabled,
    // 403 重试没有意义，页面直接显示无权限
    retry: (count, err) => !isForbiddenError(err) && count < 1,
    queryFn: () => getWorkProgressGoods({ ...params, limit: GOODS_CARD_LIMIT, offset: 0 }),
  });
}

/** 商品行 → 下钻对象：该商品 + 服务端解析的区间 + 当前季节，不限 PR。 */
export function goodsDrillTarget(
  row: WorkProgressGoodsRow,
  page: Pick<WorkProgressGoodsPage, "date_from" | "date_to">,
  season: string[] | undefined
): DrillPromotionTarget {
  return {
    title: row.display_name,
    goodsId: row.goods_id ?? undefined,
    dateFrom: page.date_from,
    dateTo: page.date_to,
    season,
  };
}
