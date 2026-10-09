// 颜色尺码明细表单的共用部分（ShipPushModal、ItemsModal）。纯函数，vitest 测。
// 每个成员款式一行（goods_members）；表单值形如 { items: { [style_id]: sku_id } }。

import type { GoodsItemIn, GoodsMember, Promotion } from "@/features/promotion/types";

export type ItemValues = Partial<Record<string, string | null | undefined>>;

/** 一个成员那一行在表单里的字段名。 */
export function itemFieldName(styleId: string): [string, string] {
  return ["items", styleId];
}

/** 成员款式；接口没给（旧缓存）时按单品回落推广单的款式。 */
export function membersOf(
  row: Pick<Promotion, "goods_members" | "style_id" | "display_short_name" | "style_short_name_snapshot">
): GoodsMember[] {
  if (row.goods_members?.length) return row.goods_members;
  const name = row.display_short_name ?? row.style_short_name_snapshot;
  return [{ style_id: row.style_id, display_short_name: name, goods_title: name }];
}

/** 打开弹窗时的初值：已有明细里属于成员的那几行。 */
export function itemsInitial(row: Pick<Promotion, "items">, members: readonly GoodsMember[]): ItemValues {
  const ids = new Set(members.map((m) => m.style_id));
  const values: ItemValues = {};
  for (const item of row.items ?? []) {
    if (ids.has(item.style_id)) values[item.style_id] = item.sku_id;
  }
  return values;
}

/** 按成员顺序组请求体；有成员没选 → null。 */
export function itemsPayload(members: readonly GoodsMember[], values: ItemValues | undefined): GoodsItemIn[] | null {
  const rows: GoodsItemIn[] = [];
  for (const m of members) {
    const sku = values?.[m.style_id];
    if (!sku) return null;
    rows.push({ style_id: m.style_id, sku_id: sku });
  }
  return rows;
}

/** 与现有明细相比有没有变（顺序无关，按款式比 sku）。 */
export function itemsChanged(current: Pick<Promotion, "items">["items"], next: readonly GoodsItemIn[]): boolean {
  const before = new Map((current ?? []).map((i) => [i.style_id, i.sku_id]));
  if (before.size !== next.length) return true;
  return next.some((r) => before.get(r.style_id) !== r.sku_id);
}

/** 没有明细的旧单按「录入信息」原文预选：只对推广单的款式（旧写法只针对它），且它得是成员。 */
export function legacyPreselectStyle(
  row: Pick<Promotion, "items" | "legacy_color_spec" | "style_id">,
  members: readonly GoodsMember[]
): string | null {
  if ((row.items ?? []).length || !(row.legacy_color_spec ?? "").trim()) return null;
  return members.some((m) => m.style_id === row.style_id) ? row.style_id : null;
}
