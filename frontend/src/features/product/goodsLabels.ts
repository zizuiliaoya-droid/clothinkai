// 商品在下拉里的显示文字（补充 2：界面不显示商品编码）。
//
// 共用的 goodsOptionLabel（编码 + 名称）不改——W1 的推广页还在用（设计 J16）；本分支的页面用这里的 helper。

import { goodsDisplayName, type GoodsOption } from "./api";

/** 平台 ID 最多列几个；再多就省略，下拉里放不下。 */
const MAX_PLATFORM_IDS = 2;

/**
 * 平台链接页「归属商品」下拉的选项文字：显示名 + 套装标记。
 *
 * 下拉里的商品都包含同一个款式，成员款号区分不了；所以**选项之间显示名相同时**，
 * 追加该商品下关联本款式的平台 ID（「· 平台ID 6652…、6653…」），没有就写「· 本款无链接」。
 * 这是对 FR-8.3「同名用成员款号区分」的偏离（设计 §11.1）。结果里从不含 goods_code。
 *
 * @param linksByGoods goods_main_id → 该商品下关联本款式的平台 ID（见 platformIdsByGoods）
 * @param options 下拉的全部选项（用来判断同名）
 */
export function goodsPickerLabel(
  option: GoodsOption,
  linksByGoods: Readonly<Record<string, readonly string[]>>,
  options: readonly GoodsOption[]
): string {
  const base = optionBaseLabel(option);
  const sameName = options.filter((o) => optionBaseLabel(o) === base).length > 1;
  if (!sameName) return base;
  const ids = linksByGoods[option.goods_main_id] ?? [];
  if (ids.length === 0) return `${base} · 本款无链接`;
  const shown = ids.slice(0, MAX_PLATFORM_IDS).join("、");
  return `${base} · 平台ID ${shown}${ids.length > MAX_PLATFORM_IDS ? " 等" : ""}`;
}

function optionBaseLabel(g: GoodsOption): string {
  const name = goodsDisplayName(g.goods_title, g.goods_short_name);
  return `${name}${g.is_suit ? "（套装）" : ""}`;
}

/** 链接列表按归属商品分组成 goods_main_id → 平台 ID（没有归属的跳过）。 */
export function platformIdsByGoods(
  links: readonly { goods_main_id: string | null; platform_id: string }[]
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const l of links) {
    if (!l.goods_main_id) continue;
    (out[l.goods_main_id] ??= []).push(l.platform_id);
  }
  return out;
}
