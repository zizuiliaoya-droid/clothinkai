import { useState } from "react";
import { Button, Card, Modal, Select, Space, Switch, Table, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { getProduction, getProductionTrend } from "@/features/report/api";
import type {
  ProductionRow,
  TimeGranularity,
  TimePreset,
} from "@/features/report/types";
import { listDictItems } from "@/features/product/api";
import { MiniLineChart } from "@/components/MiniLineChart/MiniLineChart";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import {
  ReportTimeRangeFilter,
  type ReportDateRange,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const pct = (v: string | null) =>
  v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`;
const TREND_GRANULARITY: Array<{ label: string; value: TimeGranularity }> = [
  { label: "按日", value: "day" },
  { label: "按周", value: "week" },
  { label: "按月", value: "month" },
  { label: "按年", value: "year" },
];

function trendLabel(value: string, granularity: TimeGranularity): string {
  if (granularity === "year") return value.slice(0, 4);
  if (granularity === "month") return value.slice(0, 7);
  return value;
}

export function ProductionPage() {
  const [preset, setPreset] = useState<TimePreset>("last_30d");
  const [range, setRange] = useState<ReportDateRange>(null);
  const [excludeBrushing, setExcludeBrushing] = useState(true);
  const [season, setSeason] = useState<string | undefined>(undefined);
  const [trendStyle, setTrendStyle] = useState<ProductionRow | null>(null);
  const [trendGranularity, setTrendGranularity] = useState<TimeGranularity>("day");

  const { data: seasons } = useQuery({
    queryKey: ["dict-items", "season"],
    queryFn: () => listDictItems("season"),
  });
  const seasonOptions = (seasons ?? []).map((s) => ({ label: s.value, value: s.value }));

  const { dateFrom: df, dateTo: dt, enabled } = useReportTimeRange(preset, range);

  const { data: trend, isLoading: trendLoading } = useQuery({
    queryKey: [
      "production-trend",
      trendStyle?.style_id,
      preset,
      df,
      dt,
      trendGranularity,
    ],
    enabled: !!trendStyle && enabled,
    queryFn: () =>
      getProductionTrend(trendStyle!.style_id, {
        preset,
        date_from: df,
        date_to: dt,
        granularity: trendGranularity,
      }),
  });

  const { data, isLoading } = useQuery({
    queryKey: ["production", preset, df, dt, excludeBrushing, season],
    enabled,
    queryFn: () =>
      getProduction({
        preset,
        date_from: df,
        date_to: dt,
        exclude_brushing: excludeBrushing,
        season,
      }),
  });

  // 列对齐 final.xlsx「投产报表」核心派生指标
  const baseColumns: ColumnsType<ProductionRow> = [
    {
      title: "主图",
      dataIndex: "main_image_url",
      width: 68,
      fixed: "left",
      render: (src: string | null, row) => (
        <StyleImageThumbnail src={src} alt={`${row.style_code} 款式主图`} />
      ),
    },
    { title: "货号", dataIndex: "style_code", width: 120, fixed: "left" },
    { title: "款名", dataIndex: "style_name", width: 150 },
    { title: "支付金额", dataIndex: "pay_amount", width: 110, render: money },
    { title: "退款金额", dataIndex: "refund_amount", width: 110, render: money },
    { title: "退货退款率", dataIndex: "return_rate", width: 110, render: pct },
    { title: "待确认收货金额", dataIndex: "confirmed_amount", width: 130, render: money },
    { title: "推广花费", dataIndex: "promo_cost", width: 110, render: money },
    { title: "站内投放", dataIndex: "ad_spend", width: 110, render: money },
    { title: "推广总花费", dataIndex: "total_spend", width: 120, render: money },
    { title: "总加购数", dataIndex: "add_cart_count", width: 100 },
    { title: "加购成本", dataIndex: "add_cart_cost", width: 110, render: money },
    { title: "净投产比", dataIndex: "net_roi", width: 100, render: (v) => (v == null ? "—" : v) },
    { title: "推广单件成交成本", dataIndex: "unit_deal_cost", width: 150, render: money },
    {
      title: "趋势",
      key: "trend",
      width: 80,
      fixed: "right",
      render: (_: unknown, row: ProductionRow) => (
        <Button type="link" size="small" onClick={() => setTrendStyle(row)}>
          折线图
        </Button>
      ),
    },
  ];

  // 动态展开千牛/站内导入按款式汇总的其余指标（对齐 final.xlsx 投产报表全列）
  const extraKeys = Array.from(
    new Set(
      (data?.items ?? []).flatMap((r) => Object.keys(r.extra ?? {})),
    ),
  );
  const extraColumns: ColumnsType<ProductionRow> = extraKeys.map((k) => ({
    title: k,
    key: `extra_${k}`,
    width: 130,
    render: (_: unknown, row: ProductionRow) => {
      const v = (row.extra ?? {})[k];
      return v == null || v === "" ? "—" : String(v);
    },
  }));

  const columns = [...baseColumns, ...extraColumns];
  const scrollX = 1500 + extraColumns.length * 130;

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          投产报表
        </Typography.Title>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <ReportTimeRangeFilter
          preset={preset}
          onPresetChange={setPreset}
          range={range}
          onRangeChange={setRange}
        />
        <span style={{ marginLeft: 12 }}>季节/系列：</span>
        <Select
          aria-label="季节或系列"
          value={season}
          style={{ width: 140 }}
          placeholder="全部"
          allowClear
          options={seasonOptions}
          onChange={(v) => setSeason(v)}
        />
        <span style={{ marginLeft: 12 }}>剔除刷单：</span>
        <Switch
          aria-label="剔除刷单"
          checked={excludeBrushing}
          onChange={setExcludeBrushing}
        />
      </Space>
      <Table
        rowKey="style_id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: scrollX }}
        pagination={false}
      />

      <Modal
        title={
          trendStyle
            ? `投产趋势 · ${trendStyle.style_code} ${trendStyle.style_name}`
            : "投产趋势"
        }
        open={!!trendStyle}
        onCancel={() => setTrendStyle(null)}
        footer={null}
        width={780}
        destroyOnHidden
      >
        <Space style={{ marginBottom: 16 }} wrap>
          <span>统计单位：</span>
          <Select<TimeGranularity>
            aria-label="投产趋势统计单位"
            value={trendGranularity}
            style={{ width: 110 }}
            options={TREND_GRANULARITY}
            onChange={setTrendGranularity}
          />
        </Space>
        {trendLoading ? (
          <div style={{ textAlign: "center", padding: 40 }}>加载中…</div>
        ) : (
          <MiniLineChart
            labels={(trend?.points ?? []).map((p) =>
              trendLabel(p.date, trendGranularity)
            )}
            series={[
              {
                name: "支付金额",
                color: "#1677ff",
                data: (trend?.points ?? []).map((p) => Number(p.pay_amount)),
              },
              {
                name: "站内花费",
                color: "#fa8c16",
                data: (trend?.points ?? []).map((p) => Number(p.ad_spend)),
              },
            ]}
          />
        )}
      </Modal>
    </Card>
  );
}
