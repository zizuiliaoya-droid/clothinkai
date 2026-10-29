import { formatMoney } from "@/utils/money";
// 博主 hover 卡「谈款驳回记录」（流程线设计 8.7、7c-7）的纯函数。vitest 测。不显示款号（规-1）。

import dayjs from "dayjs";
import type { NegotiationRejectionItem } from "./types";

export const NO_REJECTIONS_TEXT = "没有被驳回的谈款";
export const NO_OPINION_TEXT = "没有填写驳回意见";

/** 一条的说明行：时间 · 提报人 · 商品简称 · 平台 · 服务费（没有权限 = null，显示「—」）。缺项写「—」。 */
export function rejectionMetaLine(r: NegotiationRejectionItem): string {
  return [
    dayjs(r.rejected_at).format("YYYY-MM-DD"),
    r.pr_name || "—",
    r.display_short_name || "—",
    r.platform || "—",
    r.quote_amount == null ? "—" : formatMoney(Number(r.quote_amount).toFixed(2)),
  ].join(" · ");
}

/** 驳回意见：去两端空白，没有写回 null（界面显示 NO_OPINION_TEXT）。 */
export function rejectionOpinion(r: NegotiationRejectionItem): string | null {
  const text = (r.opinion ?? "").trim();
  return text || null;
}
