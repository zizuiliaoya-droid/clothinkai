// 谈款页列表的纯函数（流程线设计 8.3）：Tab、商品列的颜色尺码小字、主图。vitest 测。界面不显示商品编码与 SKU 编码（规-1）。

import type { GoodsImage } from "@/features/product/types";
import { colorSizeLabel } from "@/features/promotion/shipDisplay";
import { NEGOTIATION_STATUSES, type Negotiation, type NegotiationStatus } from "./types";

export const ITEM_NOT_SELECTED = "颜色尺码未选";

/** Tab 文案（3.2「界面显示」去掉「谈款中 · 」前缀）：存储值「审核驳回」显示「审批未通过」。 */
const TAB_LABEL: Partial<Record<NegotiationStatus, string>> = { 审核驳回: "审批未通过" };

/** 全部 + 7 态，顺序 = NEGOTIATION_STATUSES。key 为空 = 全部。 */
export const NEGOTIATION_TABS: { key: NegotiationStatus | ""; label: string }[] = [
  { key: "", label: "全部" },
  ...NEGOTIATION_STATUSES.map((s) => ({ key: s, label: TAB_LABEL[s] ?? s })),
];

/**
 * 商品列下面的小字：每个成员一行。套装「飞狐短裙 · 黑色 / M」，单品只写「黑色 / M」；没选的写「颜色尺码未选」。
 * 存量谈款没有明细时为空（不渲染）。
 */
export function negotiationItemLines(row: Pick<Negotiation, "items" | "goods_is_suit">): string[] {
  const items = row.items ?? [];
  const suit = row.goods_is_suit || items.length > 1;
  return items.map((i) => {
    const spec = i.sku_id ? colorSizeLabel(i.color, i.size) : ITEM_NOT_SELECTED;
    return suit ? `${i.display_short_name} · ${spec}` : spec;
  });
}

/** 主图：明细里各成员款式的主图（同款去重、没图的不占位），交给 GoodsImages；都没有时它显示「暂无主图」。 */
export function negotiationImages(row: Pick<Negotiation, "items">): GoodsImage[] {
  const seen = new Set<string>();
  const images: GoodsImage[] = [];
  for (const i of row.items ?? []) {
    if (!i.style_main_image_url || seen.has(i.style_id)) continue;
    seen.add(i.style_id);
    images.push({ style_id: i.style_id, style_code: i.style_code, url: i.style_main_image_url, source: "external" });
  }
  return images;
}
