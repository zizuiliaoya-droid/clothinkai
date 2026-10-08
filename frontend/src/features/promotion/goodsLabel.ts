import { goodsDisplayName, type GoodsOption } from "@/features/product/api";

/**
 * 推广页「归属商品」下拉的选项文字：商品简称（没填回落全称）+ 套装标记。
 *
 * **不含商品编码**：业务方 10-06 定所有界面都不显示商品编码（搜索、导出仍可用编码）。
 * 商品资料线（8a）的下拉用 `features/product/goodsLabels`，同名时再带平台 ID 区分。
 */
export function goodsNameLabel(g: GoodsOption): string {
  const name = goodsDisplayName(g.goods_title, g.goods_short_name);
  return `${name}${g.is_suit ? "（套装）" : ""}`;
}
