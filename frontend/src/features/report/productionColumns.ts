// 单品投产列定义（7e R3，报表视图设计 2.3「页面与导出共用一份列定义」）。
//
// 同步来源：backend/app/modules/report/production_columns.py 的 PRODUCTION_COLUMNS（page=True 的列）。
// PRODUCTION_PAGE_KEYS 必须与后端 PRODUCTION_PAGE_KEYS 逐项相同（backend/tests/unit/test_production_columns.py
// 的 _PAGE_KEYS 与同目录 productionColumns.test.tsx 各钉一份）；列名同后端表头，只有「商品」一列例外
// （页面把名称、渠道、链接数画在一列里，导出拆成商品简称 / 商品名称 / 渠道 / 链接数）。
// 主图、趋势是页面专有列，不在键常量里。要渲染 JSX 的格子在 components/ProductionCells.tsx。
import { createElement } from "react";
import type { ColumnsType } from "antd/es/table";
import {
  ProductionGoodsCell,
  ProductionImageCell,
  ProductionOffsiteCartCell,
  ProductionPayOrdersTitle,
  ProductionTrendButton,
} from "./components/ProductionCells";
import type { ProductionRow, Recent7Range } from "./types";

export const PRODUCTION_PAGE_KEYS = [
  "goods_short_name",
  "style_codes",
  "pay_amount",
  "pay_orders",
  "refund_amount",
  "return_rate",
  "confirmed_amount",
  "promo_cost",
  "ad_spend",
  "total_spend",
  "pre_return_roi",
  "net_roi",
  "unit_deal_cost",
  "add_cart_count",
  "add_cart_cost",
  "ad_add_cart_count",
  "ad_add_cart_cost",
  "offsite_add_cart",
  "recent7_pay_amount",
  "recent7_total_spend",
  "recent7_net_roi",
  "goods_cost_total",
  "gross_profit",
  "gross_margin_rate",
] as const satisfies ReadonlyArray<keyof ProductionRow>;

export type ProductionPageKey = (typeof PRODUCTION_PAGE_KEYS)[number];

/** 行键：一行 = 商品 × 渠道（同商品普通 / 直播两行不能撞键）。 */
export function productionRowKey(row: Pick<ProductionRow, "goods_id" | "channel">): string {
  return `${row.goods_id}|${row.channel}`;
}

type ColumnKind = "text" | "codes" | "money" | "count" | "rate" | "ratio" | "offsite";

interface ColumnSpec {
  title: string;
  kind: ColumnKind;
  width: number;
  /** 毛利三列：只给管理员（showMargin 为 false 时不渲染）。 */
  margin?: boolean;
  /** 近 7 天列组：页面放在列组「近 7 天（MM-DD ~ MM-DD）」下（导出是表头前缀）。 */
  group?: "recent7";
}

// 列名同后端 production_columns.py 的 header（商品一列除外，见文件头）
const SPECS: Record<ProductionPageKey, ColumnSpec> = {
  goods_short_name: { title: "商品", kind: "text", width: 220 },
  style_codes: { title: "含款号", kind: "codes", width: 140 },
  pay_amount: { title: "支付金额", kind: "money", width: 110 },
  pay_orders: { title: "支付件数", kind: "count", width: 110 },
  refund_amount: { title: "退款金额", kind: "money", width: 110 },
  return_rate: { title: "退货退款率", kind: "rate", width: 110 },
  confirmed_amount: { title: "实际销售金额", kind: "money", width: 130 },
  promo_cost: { title: "站外推广费", kind: "money", width: 120 },
  ad_spend: { title: "站内投放", kind: "money", width: 110 },
  total_spend: { title: "推广总花费", kind: "money", width: 120 },
  pre_return_roi: { title: "退货前投产比", kind: "ratio", width: 120 },
  net_roi: { title: "净投产比", kind: "ratio", width: 100 },
  unit_deal_cost: { title: "推广单件成交成本", kind: "money", width: 150 },
  add_cart_count: { title: "总加购数", kind: "count", width: 100 },
  add_cart_cost: { title: "总加购成本", kind: "money", width: 120 },
  ad_add_cart_count: { title: "站内加购", kind: "count", width: 100 },
  ad_add_cart_cost: { title: "站内加购成本", kind: "money", width: 120 },
  offsite_add_cart: { title: "站外加购", kind: "offsite", width: 100 },
  recent7_pay_amount: { title: "支付金额", kind: "money", width: 120, group: "recent7" },
  recent7_total_spend: { title: "推广总花费", kind: "money", width: 120, group: "recent7" },
  recent7_net_roi: { title: "净投产比", kind: "ratio", width: 110, group: "recent7" },
  goods_cost_total: { title: "销售货品总成本", kind: "money", width: 140, margin: true },
  gross_profit: { title: "单品毛利", kind: "money", width: 110, margin: true },
  gross_margin_rate: { title: "单品利润率", kind: "rate", width: 110, margin: true },
};

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const pct = (v: string | null) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`);
const plain = (v: string | number | null) => (v == null ? "—" : String(v));

/** 近 7 天列名前缀，同后端 export_header：「近 7 天（10-05 ~ 10-11）」；没有区间时只写「近 7 天」。 */
export function recent7Label(range: Recent7Range | null | undefined): string {
  if (!range) return "近 7 天";
  return `近 7 天（${range.date_from.slice(5)} ~ ${range.date_to.slice(5)}）`;
}

function renderValue(kind: ColumnKind, value: unknown) {
  switch (kind) {
    case "money":
      return money(value as string | null);
    case "rate":
      return pct(value as string | null);
    case "codes": {
      const codes = value as string[] | null | undefined;
      return codes?.length ? codes.join("、") : "—";
    }
    case "offsite":
      return createElement(ProductionOffsiteCartCell, { value: value as number | null });
    default:
      return plain(value as string | number | null);
  }
}

export interface BuildProductionColumnsOptions {
  recent7Range: Recent7Range | null | undefined;
  /** 毛利三列是否渲染（= capabilities.production_margin）；false / 能力未回来都不渲染。 */
  showMargin: boolean;
  onTrend: (row: ProductionRow) => void;
}

/** 页面列：主图 + PRODUCTION_PAGE_KEYS 顺序的指标列 + 趋势。 */
export function buildProductionColumns({
  recent7Range,
  showMargin,
  onTrend,
}: BuildProductionColumnsOptions): ColumnsType<ProductionRow> {
  const flatColumns = PRODUCTION_PAGE_KEYS.filter(
    (key) => showMargin || !SPECS[key].margin
  ).map((key) => {
    const spec = SPECS[key];
    if (key === "goods_short_name") {
      // 不显示商品编码（补充 2）；导出 Excel 仍保留「商品编码」列
      return {
        title: spec.title,
        key,
        dataIndex: key,
        width: spec.width,
        fixed: "left" as const,
        ellipsis: { showTitle: false },
        render: (_: string | null, row: ProductionRow) => createElement(ProductionGoodsCell, { row }),
      };
    }
    return {
      title: key === "pay_orders" ? createElement(ProductionPayOrdersTitle) : spec.title,
      key,
      dataIndex: key,
      width: spec.width,
      render: (value: unknown) => renderValue(spec.kind, value),
    };
  });

  // 近 7 天三列（相邻）收进一个列组：列组表头「近 7 天（MM-DD ~ MM-DD）」，子列只写指标名
  const recent7Children = flatColumns.filter((c) => SPECS[c.key].group === "recent7");
  const metricColumns: ColumnsType<ProductionRow> = [];
  for (const col of flatColumns) {
    if (SPECS[col.key].group !== "recent7") {
      metricColumns.push(col);
    } else if (col === recent7Children[0]) {
      metricColumns.push({
        title: recent7Label(recent7Range),
        key: "recent7",
        children: recent7Children,
      });
    }
  }

  return [
    {
      title: "主图",
      key: "main_image_url",
      dataIndex: "main_image_url",
      width: 68,
      fixed: "left",
      render: (_: string | null, row: ProductionRow) => createElement(ProductionImageCell, { row }),
    },
    ...metricColumns,
    {
      title: "趋势",
      key: "trend",
      width: 80,
      fixed: "right",
      render: (_: unknown, row: ProductionRow) =>
        createElement(ProductionTrendButton, { row, onTrend }),
    },
  ];
}

/** 表格横向滚动宽度：各列宽之和（列组按子列算；extra 列另加）。 */
export function productionColumnsWidth(columns: ColumnsType<ProductionRow>): number {
  return columns.reduce(
    (sum, c) =>
      sum +
      ("children" in c && c.children
        ? productionColumnsWidth(c.children as ColumnsType<ProductionRow>)
        : typeof c.width === "number"
          ? c.width
          : 0),
    0
  );
}
