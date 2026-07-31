import { useState } from "react";
import {
  Alert,
  Card,
  Col,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Typography,
} from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { MiniLineChart } from "@/components/MiniLineChart/MiniLineChart";
import {
  ReportTimeRangeFilter,
  type ReportDateRange,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";
import { StyleImageThumbnail } from "@/components/StyleImageThumbnail/StyleImageThumbnail";
import { getBiDashboard } from "@/features/report/api";
import type {
  BiStylePerformance,
  BiWorkloadRow,
  TimeGranularity,
  TimePreset,
} from "@/features/report/types";

const GRANULARITY_OPTIONS: Array<{
  label: string;
  value: TimeGranularity;
}> = [
  { label: "按日", value: "day" },
  { label: "按周", value: "week" },
  { label: "按月", value: "month" },
  { label: "按年", value: "year" },
];

const money = (value: string | null | undefined) =>
  value == null
    ? "—"
    : `¥${Number(value).toLocaleString("zh-CN", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })}`;

const percent = (value: string | null | undefined) =>
  value == null ? "—" : `${(Number(value) * 100).toFixed(1)}%`;

const roi = (value: string | null | undefined) =>
  value == null ? "—" : Number(value).toFixed(2);

function trendLabel(value: string, granularity: TimeGranularity): string {
  if (granularity === "year") return value.slice(0, 4);
  if (granularity === "month") return value.slice(0, 7);
  return value;
}

interface MoneyMetricProps {
  title: string;
  amount?: string;
  count?: number;
  description?: string;
}

function MoneyMetric({ title, amount, count, description }: MoneyMetricProps) {
  return (
    <Card size="small" style={{ height: "100%" }}>
      <Statistic
        title={title}
        value={Number(amount ?? 0)}
        precision={2}
        prefix="¥"
        valueStyle={{ color: "#0f172a", fontSize: 22 }}
      />
      <Typography.Text type="secondary">
        {count == null ? description : `${count} 篇${description ? ` · ${description}` : ""}`}
      </Typography.Text>
    </Card>
  );
}

function ProgressCell({ value, emptyText }: { value: string | null; emptyText: string }) {
  if (value == null) return <Typography.Text type="secondary">{emptyText}</Typography.Text>;
  const valuePercent = Number(value) * 100;
  return (
    <Progress
      percent={Math.min(100, Number(valuePercent.toFixed(1)))}
      size="small"
      format={() => `${valuePercent.toFixed(1)}%`}
    />
  );
}

export function BiDashboardPage() {
  const [preset, setPreset] = useState<TimePreset>("last_30d");
  const [range, setRange] = useState<ReportDateRange>(null);
  const [granularity, setGranularity] = useState<TimeGranularity>("day");
  const { dateFrom, dateTo, enabled } = useReportTimeRange(preset, range);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["bi-dashboard", preset, dateFrom, dateTo, granularity],
    enabled,
    queryFn: () =>
      getBiDashboard({
        preset,
        date_from: dateFrom,
        date_to: dateTo,
        granularity,
      }),
  });

  const workloadColumns: ColumnsType<BiWorkloadRow> = [
    { title: "负责 PR", dataIndex: "pr_name", width: 140, fixed: "left" },
    { title: "目标篇数", dataIndex: "target_count", width: 100 },
    { title: "约篇量", dataIndex: "quote_count", width: 90 },
    {
      title: "约篇进度",
      dataIndex: "quote_progress",
      width: 170,
      render: (value: string | null) => (
        <ProgressCell value={value} emptyText="未设置目标" />
      ),
    },
    { title: "发布量", dataIndex: "publish_count", width: 90 },
    {
      title: "发布进度",
      dataIndex: "publish_progress",
      width: 170,
      render: (value: string | null) => (
        <ProgressCell value={value} emptyText="暂无约篇" />
      ),
    },
    { title: "未发布", dataIndex: "pending_count", width: 90 },
    { title: "已取消", dataIndex: "cancel_count", width: 90 },
    { title: "超时", dataIndex: "overdue_count", width: 80 },
  ];

  const styleColumns: ColumnsType<BiStylePerformance> = [
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
    {
      title: "销售额",
      dataIndex: "sales_amount",
      width: 120,
      sorter: (a, b) => Number(a.sales_amount) - Number(b.sales_amount),
      render: money,
    },
    { title: "退款金额", dataIndex: "refund_amount", width: 120, render: money },
    { title: "退货率", dataIndex: "return_rate", width: 100, render: percent },
    { title: "站内花费", dataIndex: "internal_spend", width: 120, render: money },
    { title: "站外花费", dataIndex: "external_spend", width: 120, render: money },
    { title: "总花费", dataIndex: "total_spend", width: 120, render: money },
    {
      title: "ROI 投产",
      dataIndex: "roi",
      width: 110,
      sorter: (a, b) => Number(a.roi ?? -1) - Number(b.roi ?? -1),
      render: roi,
    },
  ];

  const store = data?.store_summary;
  const promotion = data?.promotion_summary;
  const periodText = data ? `${data.date_from} 至 ${data.date_to}` : "";

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Row gutter={[16, 12]} align="middle" justify="space-between">
          <Col>
            <Typography.Title level={4} style={{ margin: 0 }}>
              BI 看板
            </Typography.Title>
            <Typography.Text type="secondary">
              当前单店/租户汇总{periodText ? ` · ${periodText}` : ""}
            </Typography.Text>
          </Col>
          <Col>
            <Space wrap size={8}>
              <ReportTimeRangeFilter
                preset={preset}
                onPresetChange={setPreset}
                range={range}
                onRangeChange={setRange}
              />
              <span>趋势粒度：</span>
              <Select<TimeGranularity>
                aria-label="BI 趋势粒度"
                value={granularity}
                style={{ width: 110 }}
                options={GRANULARITY_OPTIONS}
                onChange={setGranularity}
              />
            </Space>
          </Col>
        </Row>
      </Card>

      {isError ? (
        <Alert
          type="error"
          showIcon
          message="BI 数据加载失败"
          description="请稍后重试，或检查当前账号的投产报表权限。"
        />
      ) : null}

      <Card
        title="店铺经营与推广汇总"
        loading={isLoading}
        styles={{ body: { background: "#f8fafc" } }}
      >
        <Typography.Title level={5}>店铺经营</Typography.Title>
        <Row gutter={[12, 12]}>
          <Col xs={24} sm={12} lg={8} xl={6}>
            <MoneyMetric title="总销售额" amount={store?.sales_amount} description="千牛支付金额" />
          </Col>
          <Col xs={24} sm={12} lg={8} xl={6}>
            <MoneyMetric title="退货金额" amount={store?.refund_amount} description={`退货率 ${percent(store?.return_rate)}`} />
          </Col>
          <Col xs={24} sm={12} lg={8} xl={6}>
            <MoneyMetric title="站内推广" amount={store?.internal_spend} description={`花费占比 ${percent(store?.internal_spend_ratio)}`} />
          </Col>
          <Col xs={24} sm={12} lg={8} xl={6}>
            <MoneyMetric title="站外推广" amount={store?.external_spend} description={`花费占比 ${percent(store?.external_spend_ratio)}`} />
          </Col>
          <Col xs={24} sm={12} lg={8} xl={6}>
            <MoneyMetric title="推广总花费" amount={store?.total_spend} description="站内 + 已发布站外" />
          </Col>
          <Col xs={24} sm={12} lg={8} xl={6}>
            <Card size="small" style={{ height: "100%" }}>
              <Statistic
                title="ROI 投产"
                value={store?.roi == null ? "—" : Number(store.roi)}
                precision={store?.roi == null ? undefined : 2}
                valueStyle={{ color: "#4f46e5", fontSize: 22 }}
              />
              <Typography.Text type="secondary">（销售额 - 退款）/ 总花费</Typography.Text>
            </Card>
          </Col>
        </Row>

        <Typography.Title level={5} style={{ marginTop: 24 }}>
          推广费用
        </Typography.Title>
        <Row gutter={[12, 12]}>
          <Col xs={24} sm={12} lg={6}>
            <MoneyMetric title="推广总佣金" amount={promotion?.commission_amount} count={promotion?.commission_count} />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <MoneyMetric title="推广总花费" amount={promotion?.published_spend} count={promotion?.published_count} description={`发布率 ${percent(promotion?.publish_rate)}`} />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <MoneyMetric title="未发文推广费" amount={promotion?.unpublished_spend} count={promotion?.unpublished_count} />
          </Col>
          <Col xs={24} sm={12} lg={6}>
            <MoneyMetric title="取消金额" amount={promotion?.cancelled_amount} count={promotion?.cancelled_count} />
          </Col>
        </Row>
      </Card>

      <Card title="经营趋势" loading={isLoading}>
        <MiniLineChart
          width={920}
          labels={(data?.trend ?? []).map((point) =>
            trendLabel(point.date, granularity)
          )}
          series={[
            {
              name: "销售额",
              color: "#4f46e5",
              data: (data?.trend ?? []).map((point) => Number(point.sales_amount)),
            },
            {
              name: "退款金额",
              color: "#ef4444",
              data: (data?.trend ?? []).map((point) => Number(point.refund_amount)),
            },
            {
              name: "站内花费",
              color: "#0ea5e9",
              data: (data?.trend ?? []).map((point) => Number(point.internal_spend)),
            },
            {
              name: "站外花费",
              color: "#f97316",
              data: (data?.trend ?? []).map((point) => Number(point.external_spend)),
            },
          ]}
        />
      </Card>

      <Card title="员工工作量与进度">
        <Table<BiWorkloadRow>
          rowKey={(row) => row.pr_id ?? "unassigned"}
          size="small"
          loading={isLoading}
          columns={workloadColumns}
          dataSource={data?.workload ?? []}
          scroll={{ x: 1120 }}
          pagination={false}
        />
      </Card>

      <Card title="单款表现">
        <Table<BiStylePerformance>
          rowKey="style_id"
          size="small"
          loading={isLoading}
          columns={styleColumns}
          dataSource={data?.style_performance ?? []}
          scroll={{ x: 1230 }}
          pagination={{ pageSize: 10, showSizeChanger: true }}
        />
      </Card>
    </Space>
  );
}
