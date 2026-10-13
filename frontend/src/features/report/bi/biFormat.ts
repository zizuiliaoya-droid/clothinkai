// BI 两个 Tab 共用的数字格式：金额两位小数加千分位，ROI 两位小数；null 显示「—」。
import { goodsDisplayName } from "@/features/product/api";
import type { BiGoodsRow } from "../types";

export const money = (value: string | null | undefined) =>
  value == null
    ? "—"
    : `¥${Number(value).toLocaleString("zh-CN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;
export const moneyNumber = (v: number) => money(String(v));
export const roiText = (value: string | null | undefined) =>
  value == null ? "—" : Number(value).toFixed(2);

const LABEL_MAX_STYLE_CODES = 3;

/**
 * 单品下拉的选项文字，与后端 `bi_chart_labels` 同一套规则（补充 2，§11.1）：显示名 + 套装标记，不出商品编码；
 * 显示名重复的追加成员款号「（款号 A、B）」，超过 3 个写前 3 个加「等」（评审 r1 L1）。
 */
export function goodsOptionLabels(rows: BiGoodsRow[]): string[] {
  const names = rows.map(
    (r) => goodsDisplayName(r.goods_title, r.goods_short_name) + (r.is_suit ? "（套装）" : "")
  );
  const counts = new Map<string, number>();
  names.forEach((n) => counts.set(n, (counts.get(n) ?? 0) + 1));
  return names.map((name, i) => {
    const codes = rows[i].style_codes;
    if ((counts.get(name) ?? 0) < 2 || codes.length === 0) return name;
    const more = codes.length > LABEL_MAX_STYLE_CODES ? "等" : "";
    return `${name}（款号 ${codes.slice(0, LABEL_MAX_STYLE_CODES).join("、")}${more}）`;
  });
}
