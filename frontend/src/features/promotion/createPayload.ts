import dayjs from "dayjs";
import type { PromotionCreate } from "@/features/promotion/types";

/**
 * 「新建推广」表单值 → POST /api/promotions/ 的 body。
 * `need_shipping` 没勾（或没出现）一律 false：主管直接新建多是补录历史单，不进待推送仓库（11-58）。
 */
export function buildCreatePayload(values: Record<string, unknown>): PromotionCreate {
  return {
    style_id: values.style_id as string,
    goods_main_id: (values.goods_main_id as string) || null,
    blogger_id: values.blogger_id as string,
    cooperation_mode: values.cooperation_mode as string,
    platform: values.platform as string,
    cooperation_date: dayjs(values.cooperation_date as dayjs.Dayjs).format("YYYY-MM-DD"),
    quote_amount: values.quote_amount != null ? String(values.quote_amount) : null,
    note_title: (values.note_title as string) || null,
    remark: (values.remark as string) || null,
    need_shipping: values.need_shipping === true,
  };
}
