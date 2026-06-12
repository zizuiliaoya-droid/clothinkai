import { useState } from "react";
import {
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
import {
  getProduction,
  getPublishSummary,
  getStoreDaily,
} from "@/features/report/api";
import type { ProductionRow } from "@/features/report/types";

const PRESETS = [
  { label: "近7天", value: "last_7d" },
  { label: "近30天", value: "last_30d" },
  { label: "本月", value: "this_month" },
  { label: "上月", value: "last_month" },
];
const num = (v: string | null | undefined) => (v == null ? 0 : Number(v));

export function BiDashboardPage() {
  const [preset, setPreset] = useState("last_30d");

  const { data: summary } = useQuery({
    queryKey: ["bi-summary", preset],
    queryFn: () => getPublishSummary({ preset }),
  });
  const { data: production } = useQuery({
    queryKey: ["bi-production", preset],
    queryFn: () => getProduction({ preset }),
  });
  const { data: store } = useQuery({
    queryKey: ["bi-store", preset],
    queryFn: () => getStoreDaily({ preset }),
  });

  const totalPay = (store ?? []).reduce((s, r) => s + num(r.pay_amount), 0);
  const totalSpend = (production?.items ?? []).reduce(
    (s, r) => s + num(r.total_spend),
    0
  );
  const publishRate = summary?.publish_rate ? num(summary.publish_rate) * 100 : 0;
  const overdueRate = summary?.overdue_rate ? num(summary.overdue_rate) * 100 : 0;

  // 投产 Top（按支付金额）
  const topProduction = [...(production?.items ?? [])]
    .sort((a, b) => num(b.pay_amount) - num(a.pay_amount))
    .slice(0, 10);

  const prodColumns: ColumnsType<ProductionRow> = [
    { title: "款号", dataIndex: "style_code", width: 120 },
    { title: "款名", dataIndex: "style_name" },
    {
      title: "支付金额",
      dataIndex: "pay_amount",
      width: 120,
      render: (v) => `¥${v}`,
    },
    {
      title: "净投产比",
      dataIndex: "net_roi",
      width: 100,
      render: (v) => (v == null ? "—" : v),
    },
    {
      title: "退货退款率",
      dataIndex: "return_rate",
      width: 110,
      render: (v: string | null) =>
        v == null ? "—" : `${(num(v) * 100).toFixed(1)}%`,
    },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            BI 看板
          </Typography.Title>
          <Select
            value={preset}
            style={{ width: 130, marginLeft: 16 }}
            options={PRESETS}
            onChange={setPreset}
          />
        </Space>
        <Row gutter={16}>
          <Col span={6}>
            <Statistic title="约篇量" value={summary?.quote_count ?? 0} />
          </Col>
          <Col span={6}>
            <Statistic title="发布量" value={summary?.publish_count ?? 0} />
          </Col>
          <Col span={6}>
            <Statistic title="点赞量" value={summary?.like_count ?? 0} />
          </Col>
          <Col span={6}>
            <Statistic title="取消量" value={summary?.cancel_count ?? 0} />
          </Col>
        </Row>
        <Row gutter={16} style={{ marginTop: 24 }}>
          <Col span={6}>
            <Statistic title="店铺支付金额" value={totalPay} precision={2} prefix="¥" />
          </Col>
          <Col span={6}>
            <Statistic title="推广总花费" value={totalSpend} precision={2} prefix="¥" />
          </Col>
          <Col span={6}>
            <Typography.Text type="secondary">发布率</Typography.Text>
            <Progress percent={Number(publishRate.toFixed(1))} status="active" />
          </Col>
          <Col span={6}>
            <Typography.Text type="secondary">超时率</Typography.Text>
            <Progress
              percent={Number(overdueRate.toFixed(1))}
              status={overdueRate > 20 ? "exception" : "normal"}
            />
          </Col>
        </Row>
      </Card>

      <Card
        title={
          <Typography.Title level={5} style={{ margin: 0 }}>
            投产 Top 10（按支付金额）
          </Typography.Title>
        }
      >
        <Table
          rowKey="style_id"
          size="small"
          columns={prodColumns}
          dataSource={topProduction}
          pagination={false}
        />
      </Card>
    </Space>
  );
}
