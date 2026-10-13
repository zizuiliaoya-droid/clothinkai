// 工作进度的指标列（7e R5）：PR 表与周明细共用，首列（负责PR / 所在周）由各表自己加。
//
// 列对齐 final.xlsx「工作进度表」，R5 加 PRD 公式与 R2 落盘的几列；「成本(含衣服)」改为「推广成本」（口径变大）。
// 页面只展示这些新列：约稿 / 已发布服务费、单篇均价、两个金额发布率、已发货、发货发布率、推广成本、CPL，其余只在接口与导出。
import type { ColumnsType } from "antd/es/table";
import { HeaderHint } from "../components/HeaderHint";
import type { WorkProgressMetrics } from "../types";

/** 比率显示成百分比（一位小数）；null →「—」。 */
export const pct = (v: string | null) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`);
/** 金额加 ¥；读不到（null）→「—」。 */
export const money = (v: string | null) => (v == null ? "—" : `¥${v}`);

export const LIKE_HINT = "折算点赞：按系统设置 · 平台指标的系数折算（默认抖音 / 快手 ×0.1）";
export const TOTAL_AMOUNT_PUBLISH_RATE_HINT = "已发布服务费 ÷ 有效约稿服务费（扣取消与召回）";
export const PROMO_COST_HINT =
  "站外推广成本：含服务费、样品、运费，不看发布状态（未发布、已召回的单也算）";
export const CPL_HINT = "推广成本 ÷ 折算点赞";

/** 指标列（不含首列）。泛型给 PR 行、周行共用。 */
export function workProgressMetricColumns<T extends WorkProgressMetrics>(): ColumnsType<T> {
  return [
    { title: "约篇件数", dataIndex: "quote_count", width: 90 },
    { title: "档期内", dataIndex: "in_schedule_count", width: 80 },
    { title: "催发", dataIndex: "urge_count", width: 70 },
    { title: "重要催发", dataIndex: "important_urge_count", width: 90 },
    { title: "超时", dataIndex: "overdue_count", width: 70 },
    { title: "已发布", dataIndex: "publish_count", width: 80 },
    { title: "已填写点赞量数量", dataIndex: "info_complete_count", width: 140 },
    { title: "信息完整度", dataIndex: "info_complete_rate", width: 100, render: pct },
    { title: "已取消", dataIndex: "cancel_count", width: 80 },
    { title: "应召回", dataIndex: "recall_due_count", width: 80 },
    { title: "召回成功", dataIndex: "recall_success_count", width: 90 },
    { title: "召回完成率", dataIndex: "recall_complete_rate", width: 100, render: pct },
    { title: "超时率", dataIndex: "overdue_rate", width: 90, render: pct },
    { title: "月度完成率", dataIndex: "month_complete_rate", width: 100, render: pct },
    { title: "爆文数", dataIndex: "hit_count", width: 80 },
    { title: "爆文率", dataIndex: "hit_rate", width: 90, render: pct },
    {
      title: <HeaderHint label="点赞数" hint={LIKE_HINT} />,
      dataIndex: "like_count",
      width: 90,
    },
    { title: "约稿服务费", dataIndex: "quote_amount", width: 110, render: money },
    { title: "已发布服务费", dataIndex: "published_quote_amount", width: 120, render: money },
    { title: "发布单篇均价", dataIndex: "avg_published_quote", width: 120, render: money },
    { title: "金额发布率", dataIndex: "amount_publish_rate", width: 100, render: pct },
    {
      title: <HeaderHint label="总金额发布率" hint={TOTAL_AMOUNT_PUBLISH_RATE_HINT} />,
      dataIndex: "total_amount_publish_rate",
      width: 120,
      render: pct,
    },
    { title: "已发货", dataIndex: "shipped_count", width: 80 },
    { title: "发货发布率", dataIndex: "ship_publish_rate", width: 100, render: pct },
    {
      title: <HeaderHint label="推广成本" hint={PROMO_COST_HINT} />,
      dataIndex: "promo_cost",
      width: 120,
      render: money,
    },
    {
      title: <HeaderHint label="CPL(元/赞)" hint={CPL_HINT} />,
      dataIndex: "cpl",
      width: 110,
      render: money,
    },
  ];
}

/** 列宽之和（表格横向滚动用）。 */
export function columnsWidth<T>(columns: ColumnsType<T>): number {
  return columns.reduce((sum, c) => sum + (Number(c.width) || 0), 0);
}
