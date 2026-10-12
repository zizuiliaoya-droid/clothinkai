// 店铺日 / 周 / 月表格列（7e R4，设计 2.4）。三页共用一份，只有「日期 / 周期」一列按粒度显示。
//
// 列名：「访客数」改名「商品访客数合计」（默 16，悬停说明会重复计）；「支付订单数」改正为「支付件数」。
// 比率都在后端分桶求和后现算，分母为 0 / 负时为 null，这里显示「—」。「其中直播」两列只展示。
// 下钻（S9）：月行 → 周表、周行 → 日表，带行上实际起止（storeDrillHref）。
// 日表能写时（S8）手填三项与仅退款在编辑行里换成金额框，末尾多一列「操作」。
import type { ColumnsType } from "antd/es/table";
import { Link } from "react-router-dom";
import { HeaderHint } from "../components/HeaderHint";
import type { StoreGranularity, StoreReportRow } from "../types";
import { RefundOnlyRateCell } from "./StoreCells";
import { ManualActionsCell, ManualAmountInput, type StoreManualEditing } from "./StoreManualCells";
import type { StoreManualField } from "./storeManual";
import { pct } from "./storeFormat";

export const VISITORS_HINT = "按商品累加：同一访客看了多个商品会重复计";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const roi = (v: string | null) => (v == null ? "—" : Number(v).toFixed(2));

/** 行上的周期：日「YYYY-MM-DD」，周「MM-DD ~ MM-DD」（实际起止），月「YYYY-MM」。 */
export function storePeriodLabel(row: StoreReportRow, granularity: StoreGranularity): string {
  if (granularity === "month") return row.date_from.slice(0, 7);
  if (granularity === "week") return `${row.date_from.slice(5)} ~ ${row.date_to.slice(5)}`;
  return row.date_from;
}

/**
 * 下钻地址：月行 → 周表、周行 → 日表，带行上的实际起止（桶 ∩ 请求区间，不是桶首日 / 整月）；日行没有下一级。
 * 下一级页面由 useReportFilters 从 URL 读起止（不写进筛选记忆）。
 */
export function storeDrillHref(row: StoreReportRow, granularity: StoreGranularity): string | null {
  if (granularity === "day") return null;
  const params = new URLSearchParams({
    preset: "custom",
    date_from: row.date_from,
    date_to: row.date_to,
  });
  return `/store/${granularity === "month" ? "weekly" : "daily"}?${params.toString()}`;
}

function extraColumns(rows: StoreReportRow[]): ColumnsType<StoreReportRow> {
  const keys = new Set<string>();
  for (const r of rows) Object.keys(r.extra ?? {}).forEach((k) => keys.add(k));
  return Array.from(keys).map((k) => ({
    title: k,
    key: `ex_${k}`,
    width: 130,
    render: (_: unknown, r: StoreReportRow) => {
      const v = (r.extra ?? {})[k];
      return v == null || v === "" ? "—" : String(v);
    },
  }));
}

function manualColumn(
  field: StoreManualField,
  title: string,
  width: number,
  display: (v: string | null) => string,
  edit: StoreManualEditing | undefined,
  autoFocus = false
): ColumnsType<StoreReportRow>[number] {
  return {
    title,
    key: field,
    dataIndex: field,
    width,
    render: (v: string | null, r: StoreReportRow) =>
      edit && edit.day === r.date_from ? (
        <ManualAmountInput edit={edit} row={r} field={field} label={title} autoFocus={autoFocus} />
      ) : (
        display(v)
      ),
  };
}

/** `edit` 只在日表且能写时传：手填四列可编辑，末尾加「操作」列。 */
export function buildStoreColumns(
  granularity: StoreGranularity,
  rows: StoreReportRow[],
  edit?: StoreManualEditing
): ColumnsType<StoreReportRow> {
  return [
    {
      title: granularity === "day" ? "日期" : "周期",
      key: "period",
      width: granularity === "week" ? 140 : 120,
      fixed: "left",
      // 周 / 月：周期是链接（键盘下钻走它）；整行点击由 StoreReportView 的 onRow 处理
      render: (_: unknown, r: StoreReportRow) => {
        const href = storeDrillHref(r, granularity);
        const label = storePeriodLabel(r, granularity);
        return href ? <Link to={href}>{label}</Link> : label;
      },
    },
    {
      title: <HeaderHint label="商品访客数合计" hint={VISITORS_HINT} />,
      key: "visitors",
      dataIndex: "visitors",
      width: 140,
    },
    { title: "支付金额", key: "pay_amount", dataIndex: "pay_amount", width: 120, render: money },
    { title: "支付件数", key: "pay_orders", dataIndex: "pay_orders", width: 100 },
    { title: "退款金额", key: "refund_amount", dataIndex: "refund_amount", width: 110, render: money },
    { title: "刷单本金", key: "brushing_amount", dataIndex: "brushing_amount", width: 110, render: money },
    { title: "实际销售额", key: "actual_sales", dataIndex: "actual_sales", width: 120, render: money },
    { title: "万相台", key: "ad_spend", dataIndex: "ad_spend", width: 110, render: money },
    manualColumn("ad_spend_total", "全站推消耗", 120, money, edit, true),
    manualColumn("zhitongche_spend", "直通车消耗", 120, money, edit),
    manualColumn("yinli_spend", "引力魔方消耗", 130, money, edit),
    { title: "站内推广费", key: "internal_spend", dataIndex: "internal_spend", width: 120, render: money },
    { title: "站外推广成本", key: "promo_cost", dataIndex: "promo_cost", width: 130, render: money },
    { title: "推广总额", key: "total_spend", dataIndex: "total_spend", width: 110, render: money },
    { title: "推广总占比", key: "total_spend_ratio", dataIndex: "total_spend_ratio", width: 110, render: pct },
    { title: "站内占比", key: "internal_ratio", dataIndex: "internal_ratio", width: 100, render: pct },
    { title: "站外占比", key: "external_ratio", dataIndex: "external_ratio", width: 100, render: pct },
    { title: "净推广占比", key: "net_spend_ratio", dataIndex: "net_spend_ratio", width: 110, render: pct },
    { title: "净投产", key: "net_roi", dataIndex: "net_roi", width: 100, render: roi },
    { title: "投产（扣除刷单）", key: "roi_ex_brushing", dataIndex: "roi_ex_brushing", width: 140, render: roi },
    manualColumn(
      "refund_only_amount",
      "仅退款",
      110,
      (v) => (v == null ? "未录" : `¥${v}`),
      edit
    ),
    {
      title: "仅退款率",
      key: "refund_only_rate",
      width: 130,
      render: (_: unknown, r: StoreReportRow) => (
        <RefundOnlyRateCell row={r} granularity={granularity} />
      ),
    },
    { title: "其中直播支付", key: "live_pay_amount", dataIndex: "live_pay_amount", width: 130, render: money },
    { title: "其中直播站内花费", key: "live_ad_spend", dataIndex: "live_ad_spend", width: 150, render: money },
    ...extraColumns(rows),
    ...(edit
      ? [
          {
            title: "操作",
            key: "actions",
            width: 130,
            fixed: "right" as const,
            render: (_: unknown, r: StoreReportRow) => <ManualActionsCell edit={edit} row={r} />,
          },
        ]
      : []),
  ];
}

/** 表格横向滚动宽度：各列宽之和。 */
export function storeColumnsWidth(columns: ColumnsType<StoreReportRow>): number {
  return columns.reduce((sum, c) => sum + (typeof c.width === "number" ? c.width : 0), 0);
}
