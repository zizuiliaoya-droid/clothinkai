// BI Tab1 店铺总览（7e R6，设计 2.6）：店铺 KPI 8 卡 + 推广费用 4 卡 + 经营趋势 + 净投产 Top10 柱 + 支付占比饼
// + PR 工作量表 + 单品表。下钻能否点看 capabilities，不能点时标签可聚焦、悬停 / 聚焦出提示。
import type { ReactNode } from "react";
import { Card, Col, Progress, Row, Table, Tag, Tooltip, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { Link, useNavigate } from "react-router-dom";
import { BarChart, type BarDatum } from "@/components/Charts/BarChart";
import { PieChart, type PieDatum } from "@/components/Charts/PieChart";
import { GoodsNameCell } from "@/components/GoodsNameCell/GoodsNameCell";
import { MiniLineChart } from "@/components/MiniLineChart/MiniLineChart";
import { ChartFrame } from "@/components/Charts/ChartFrame";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import { goodsDisplayName } from "@/features/product/api";
import type {
  BiChartDatum,
  BiDashboard,
  BiGoodsRow,
  BiWorkloadRow,
  ReportCapabilities,
  TimeGranularity,
} from "../types";
import {
  NEED_PRODUCTION_HINT,
  NEED_WORK_PROGRESS_HINT,
  canDrillProduction,
  canDrillWorkProgress,
  productionDrillHref,
  workProgressDrillHref,
  type BiDrillRange,
} from "./biDrill";
import { money, moneyNumber, roiText } from "./biFormat";

/** 次要文字：slate-600，白底约 7.6:1。 */
export const MUTED = "#475569";
const VISUALLY_HIDDEN = {
  position: "absolute",
  width: 1,
  height: 1,
  padding: 0,
  margin: -1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
  border: 0,
} as const;
/** 饼图「其他」项的键：没有 goods_id，不可下钻。 */
const OTHER_KEY = "__other__";

const percent = (value: string | null | undefined) =>
  value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;

/** 趋势横轴：行上是实际起止（桶 ∩ 区间）；周写「MM-DD~MM-DD」，月 / 年取前缀。 */
function trendLabel(from: string, to: string, granularity: TimeGranularity): string {
  if (granularity === "year") return from.slice(0, 4);
  if (granularity === "month") return from.slice(0, 7);
  if (granularity === "week") return `${from.slice(5)}~${to.slice(5)}`;
  return from;
}

/** 不可下钻的标签：可聚焦（Tab 停一次），悬停 / 聚焦出提示；读屏读视觉隐藏的说明。 */
function NoDrillLabel({ label, hint }: { label: ReactNode; hint: string }) {
  return (
    <Tooltip title={hint} trigger={["hover", "focus"]}>
      <span tabIndex={0} style={{ cursor: "help", borderBottom: `1px dotted ${MUTED}` }}>
        <span>{label}</span>
        <span style={VISUALLY_HIDDEN}>（{hint}）</span>
      </span>
    </Tooltip>
  );
}

export function KpiCard({ title, value, description }: { title: string; value: string; description?: string }) {
  return (
    <Card size="small" style={{ height: "100%" }}>
      <Typography.Text style={{ color: MUTED }}>{title}</Typography.Text>
      <div style={{ color: "#0f172a", fontSize: 22, fontWeight: 600, lineHeight: 1.4 }}>{value}</div>
      {description ? <Typography.Text style={{ color: MUTED }}>{description}</Typography.Text> : null}
    </Card>
  );
}

function ProgressCell({ value, emptyText }: { value: string | null; emptyText: string }) {
  if (value == null) return <Typography.Text style={{ color: MUTED }}>{emptyText}</Typography.Text>;
  const valuePercent = Number(value) * 100;
  return (
    <Progress
      percent={Math.min(100, Number(valuePercent.toFixed(1)))}
      size="small"
      format={() => `${valuePercent.toFixed(1)}%`}
    />
  );
}

interface Props {
  data: BiDashboard | undefined;
  loading: boolean;
  caps: ReportCapabilities | undefined;
}

export function StoreOverviewTab({ data, loading, caps }: Props) {
  const navigate = useNavigate();
  const canProduction = canDrillProduction(caps);
  const canWorkProgress = canDrillWorkProgress(caps);
  const range: BiDrillRange | null = data
    ? { date_from: data.date_from, date_to: data.date_to, exclude_brushing: data.exclude_brushing }
    : null;
  const store = data?.store.summary;
  const promotion = data?.store.promotion;
  const trend = data?.store.trend ?? [];
  const granularity = data?.granularity ?? "day";

  /** 柱 / 饼表格里的商品标签：能下钻 → 链接；没权限 → 带提示的文本；「其他」→ 纯文本。 */
  const chartLabel = (d: { key: string; label: string }) => {
    if (d.key === OTHER_KEY || !range) return d.label;
    return canProduction ? (
      <Link to={productionDrillHref(range, d.key)}>{d.label}</Link>
    ) : (
      <NoDrillLabel label={d.label} hint={NEED_PRODUCTION_HINT} />
    );
  };
  const chartSelect = (d: { key: string }) => {
    if (range && d.key !== OTHER_KEY) navigate(productionDrillHref(range, d.key));
  };
  const chartKey = (d: BiChartDatum) => d.goods_id ?? OTHER_KEY;
  const roiBars: BarDatum[] = (data?.goods.roi_top ?? []).map((d) => ({
    key: chartKey(d),
    label: d.label,
    value: Number(d.value),
  }));
  const paySlices: PieDatum[] = (data?.goods.pay_share ?? []).map((d) => ({
    key: chartKey(d),
    label: d.label,
    value: Number(d.value),
  }));

  const workloadColumns: ColumnsType<BiWorkloadRow> = [
    {
      title: "负责 PR",
      dataIndex: "pr_name",
      width: 140,
      fixed: "left",
      render: (name: string, row) =>
        !range ? (
          name
        ) : canWorkProgress ? (
          <Link to={workProgressDrillHref(range, row.pr_id)}>{name}</Link>
        ) : (
          <NoDrillLabel label={name} hint={NEED_WORK_PROGRESS_HINT} />
        ),
    },
    { title: "目标篇数", dataIndex: "target_count", width: 100 },
    { title: "约篇量", dataIndex: "quote_count", width: 90 },
    {
      title: "约篇进度",
      dataIndex: "quote_progress",
      width: 170,
      render: (value: string | null) => <ProgressCell value={value} emptyText="未设置目标" />,
    },
    { title: "发布量", dataIndex: "publish_count", width: 90 },
    {
      title: "发布进度",
      dataIndex: "publish_progress",
      width: 170,
      render: (value: string | null) => <ProgressCell value={value} emptyText="暂无约篇" />,
    },
    { title: "未发布", dataIndex: "pending_count", width: 90 },
    { title: "已取消", dataIndex: "cancel_count", width: 90 },
    { title: "超时", dataIndex: "overdue_count", width: 80 },
  ];

  const goodsColumns: ColumnsType<BiGoodsRow> = [
    {
      title: "主图",
      dataIndex: "main_image_url",
      width: 68,
      fixed: "left",
      render: (src: string | null, row) => (
        <StyleImageThumbnail
          src={src}
          alt={`${goodsDisplayName(row.goods_title, row.goods_short_name)} 商品主图`}
        />
      ),
    },
    {
      // 不显示商品编码（补充 2）；点名称跳单品投产（要投产读权限）
      title: "商品",
      dataIndex: "goods_short_name",
      width: 220,
      fixed: "left",
      ellipsis: { showTitle: false },
      render: (_: string | null, row) => {
        const name = <GoodsNameCell goodsTitle={row.goods_title} shortName={row.goods_short_name} />;
        return (
          <>
            {row.is_suit && (
              <Tag color="purple" style={{ marginInlineEnd: 4 }}>
                套装
              </Tag>
            )}
            {!range ? (
              name
            ) : canProduction ? (
              <Link to={productionDrillHref(range, row.goods_id)}>{name}</Link>
            ) : (
              <NoDrillLabel
                label={goodsDisplayName(row.goods_title, row.goods_short_name)}
                hint={NEED_PRODUCTION_HINT}
              />
            )}
          </>
        );
      },
    },
    {
      title: "支付金额",
      dataIndex: "pay_amount",
      width: 120,
      sorter: (a, b) => Number(a.pay_amount) - Number(b.pay_amount),
      render: money,
    },
    { title: "退款金额", dataIndex: "refund_amount", width: 120, render: money },
    { title: "退货率", dataIndex: "return_rate", width: 100, render: percent },
    { title: "实际销售金额", dataIndex: "confirmed_amount", width: 130, render: money },
    { title: "站内花费", dataIndex: "internal_spend", width: 120, render: money },
    { title: "站外花费", dataIndex: "external_spend", width: 120, render: money },
    { title: "总花费", dataIndex: "total_spend", width: 120, render: money },
    {
      title: "净投产比",
      dataIndex: "net_roi",
      width: 110,
      sorter: (a, b) => Number(a.net_roi ?? -Infinity) - Number(b.net_roi ?? -Infinity),
      render: roiText,
    },
  ];

  const kpis: Array<{ title: string; value: string; description?: string }> = [
    { title: "支付总额", value: money(store?.pay_amount), description: "千牛支付金额" },
    { title: "退款金额", value: money(store?.refund_amount), description: `退货率 ${percent(store?.return_rate)}` },
    { title: "刷单本金", value: money(store?.brushing_amount) },
    { title: "实际销售额", value: money(store?.actual_sales), description: "支付 − 退款 − 刷单本金" },
    {
      title: "站内推广费",
      value: money(store?.internal_spend),
      description: `站内占比 ${percent(store?.internal_spend_ratio)}`,
    },
    {
      title: "站外推广成本",
      value: money(store?.external_spend),
      description: `站外占比 ${percent(store?.external_spend_ratio)}`,
    },
    {
      title: "推广总额",
      value: money(store?.total_spend),
      description: `推广总占比 ${percent(store?.total_spend_ratio)}`,
    },
    {
      title: "净投产",
      value: roiText(store?.net_roi),
      description: `投产（扣除刷单）${roiText(store?.roi_ex_brushing)}`,
    },
  ];
  const promotionCards: Array<{ title: string; amount: string | null | undefined; count?: number; extra?: string }> = [
    { title: "推广总佣金", amount: promotion?.commission_amount, count: promotion?.commission_count },
    {
      title: "约篇服务费",
      amount: promotion?.published_spend,
      count: promotion?.published_count,
      extra: `发布率 ${percent(promotion?.publish_rate)}`,
    },
    { title: "未发文推广费", amount: promotion?.unpublished_spend, count: promotion?.unpublished_count },
    { title: "取消金额", amount: promotion?.cancelled_amount, count: promotion?.cancelled_count },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card title="店铺经营与推广汇总" loading={loading} styles={{ body: { background: "#f8fafc" } }}>
        <Typography.Title level={5}>店铺经营</Typography.Title>
        <Row gutter={[12, 12]}>
          {kpis.map((k) => (
            <Col key={k.title} xs={24} sm={12} lg={6}>
              <KpiCard {...k} />
            </Col>
          ))}
        </Row>
        <Typography.Title level={5} style={{ marginTop: 24 }}>
          推广费用
        </Typography.Title>
        <Row gutter={[12, 12]}>
          {promotionCards.map((c) => (
            <Col key={c.title} xs={24} sm={12} lg={6}>
              <KpiCard
                title={c.title}
                value={money(c.amount)}
                description={c.count == null ? c.extra : `${c.count} 篇${c.extra ? ` · ${c.extra}` : ""}`}
              />
            </Col>
          ))}
        </Row>
      </Card>

      <Card title="经营趋势" loading={loading}>
        <ChartFrame title="经营趋势" columns={["期间", "支付金额", "退款金额", "实际销售额", "站内花费", "站外花费", "推广总额"]}
          rows={trend.map((point) => ({ key: point.date_from, cells: [
            `${point.date_from} 至 ${point.date_to}`, money(point.pay_amount), money(point.refund_amount),
            money(point.actual_sales), money(point.internal_spend), money(point.external_spend), money(point.total_spend),
          ] }))} empty={!trend.length} emptyText="暂无趋势数据">
        {/* 跟着卡片宽度画：375 宽不用横向滚动（评审 r1 L2） */}
        <MiniLineChart
          fluid
          width={920}
          ariaLabel="经营趋势折线图"
          labels={trend.map((p) => trendLabel(p.date_from, p.date_to, granularity))}
          series={[
            { name: "支付金额", color: "#4f46e5", data: trend.map((p) => Number(p.pay_amount)) },
            { name: "退款金额", color: "#ef4444", data: trend.map((p) => Number(p.refund_amount)) },
            { name: "实际销售额", color: "#16a34a", data: trend.map((p) => Number(p.actual_sales)) },
            { name: "站内花费", color: "#0ea5e9", data: trend.map((p) => Number(p.internal_spend)) },
            { name: "站外花费", color: "#f97316", data: trend.map((p) => Number(p.external_spend)) },
            { name: "推广总额", color: "#7c3aed", data: trend.map((p) => Number(p.total_spend)) },
          ]}
        />
        </ChartFrame>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="净投产 Top10" loading={loading} style={{ height: "100%" }}>
            <BarChart
              title="净投产 Top10"
              data={roiBars}
              labelHeader="商品"
              valueLabel="净投产"
              formatValue={(v) => v.toFixed(2)}
              onSelect={chartSelect}
              canSelect={() => canProduction}
              renderLabel={chartLabel}
              emptyText="这段时间没有可算净投产的商品"
            />
          </Card>
        </Col>
        <Col xs={24} lg={12}>
          <Card title="支付占比" loading={loading} style={{ height: "100%" }}>
            <PieChart
              title="支付占比"
              data={paySlices}
              labelHeader="商品"
              valueLabel="支付金额"
              formatValue={moneyNumber}
              onSelect={chartSelect}
              canSelect={(d) => canProduction && d.key !== OTHER_KEY}
              renderLabel={chartLabel}
              emptyText="这段时间没有支付"
            />
          </Card>
        </Col>
      </Row>

      <Card title="员工工作量与进度">
        <Table<BiWorkloadRow>
          rowKey={(row) => row.pr_id ?? "unassigned"}
          size="small"
          loading={loading}
          columns={workloadColumns}
          dataSource={data?.store.workload ?? []}
          scroll={{ x: 1120 }}
          pagination={false}
        />
      </Card>

      <Card title="单品表现">
        <Table<BiGoodsRow>
          rowKey="goods_id"
          size="small"
          loading={loading}
          columns={goodsColumns}
          dataSource={data?.goods.items ?? []}
          scroll={{ x: 1260 }}
          pagination={{ pageSize: 10, showSizeChanger: true }}
        />
      </Card>
    </div>
  );
}
