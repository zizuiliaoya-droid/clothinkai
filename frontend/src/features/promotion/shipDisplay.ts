// 推广列表的颜色尺码小字与发货悬停内容（流程线 8.4）。纯函数，vitest 测；不出现 SKU 编码（规-1）。

import dayjs from "dayjs";
import type { Promotion } from "./types";

/** 「黑色 / M」；缺一项只显示另一项，都没有回「—」。 */
export function colorSizeLabel(color: string | null | undefined, size: string | null | undefined): string {
  const parts = [color, size].map((v) => (v ?? "").trim()).filter(Boolean);
  return parts.length ? parts.join(" / ") : "—";
}

export interface ItemSpec {
  lines: string[];
  /** true = 没有明细的旧单，显示的是「录入信息」里的颜色及规格原文。 */
  legacy: boolean;
}

/**
 * 品名下的颜色尺码：单品一行「黑色 / M」；套装每个成员一行「简称 · 黑色 / M」；
 * 没有明细的旧单显示 legacy_color_spec 原文（调用方另标「旧」）；都没有 → null。
 */
export function itemSpecLines(
  row: Partial<Pick<Promotion, "items" | "legacy_color_spec">>
): ItemSpec | null {
  const items = row.items ?? [];
  if (items.length === 1) {
    return { lines: [colorSizeLabel(items[0].color, items[0].size)], legacy: false };
  }
  if (items.length > 1) {
    return {
      lines: items.map((i) => `${i.display_short_name} · ${colorSizeLabel(i.color, i.size)}`),
      legacy: false,
    };
  }
  const legacy = (row.legacy_color_spec ?? "").trim();
  return legacy ? { lines: [legacy], legacy: true } : null;
}

const fmt = (v: string | null | undefined) => (v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "—");

/** 「发货」列悬停：待打单 = 推送人与时间；已发货 = 快递公司 + 单号 + 发货时间；其余没有。 */
export function shipDetailLines(
  row: Pick<
    Promotion,
    | "ship_status"
    | "ship_pushed_at"
    | "ship_pushed_by_name"
    | "ship_courier"
    | "ship_waybill"
    | "shipped_at"
  >
): string[] {
  if (row.ship_status === "待打单") {
    return [`推送人：${row.ship_pushed_by_name || "—"}`, `推送时间：${fmt(row.ship_pushed_at)}`];
  }
  if (row.ship_status === "已发货") {
    return [
      `快递公司：${row.ship_courier || "—"}`,
      `单号：${row.ship_waybill || "—"}`,
      `发货时间：${fmt(row.shipped_at)}`,
    ];
  }
  return [];
}

/**
 * 「改归属商品」弹窗的提示（11-53）：推送前（历史单 / 待发货）换商品会清空颜色尺码，推送时按新商品重选；
 * 推送之后（待打单 / 已发货）只改投产报表归属，明细不动。与后端 update_promotion 同口径。
 */
export function changeGoodsHint(shipStatus: Promotion["ship_status"] | undefined): string {
  return shipStatus === "待打单" || shipStatus === "已发货"
    ? "只改报表归属，不改已发出的颜色尺码"
    : "换商品后，推送时要重新选颜色尺码";
}
