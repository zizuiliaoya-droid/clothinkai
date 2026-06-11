import { useState } from "react";
import {
  Card,
  Col,
  Input,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Typography,
} from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { getPublishCards, getPublishSummary } from "@/features/report/api";
import type { StyleCard } from "@/features/report/types";

const PRESETS = [
  { label: "近7天", value: "last_7d" },
  { label: "近30天", value: "last_30d" },
  { label: "本月", value: "this_month" },
  { label: "上月", value: "last_month" },
];
const pct = (v: string | null) =>
  v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`;
const money = (v: string | null) => (v == null ? "—" : `¥${v}`);

export function PublishProgressPage() {
  const [preset, setPreset] = useState("last_30d");
  const [keyword, setKeyword] = useState<string | undefined>();
  const [page, setPage] = useState(1);

  const { data: summary } = useQuery({
    queryKey: ["publish-summary", preset],
    queryFn: () => getPublishSummary({ preset }),
  });
  const { data: cards, isLoading } = useQuery({
    queryKey: ["publish-cards", preset, keyword, page],
    queryFn: () =>
      getPublishCards({ preset, keyword, page, page_size: 12 }),
  });

  // Layer2 商品卡片（表格呈现）
  const columns: ColumnsType<StyleCard> = [
    { title: "款号", dataIndex: "style_code", width: 120, fixed: "left" },
    { title: "品名", dataIndex: "style_name", width: 160 },
    { title: "成本", dataIndex: "cost", width: 90, render: money },
    { title: "约篇量", dataIndex: "quote_count", width: 80 },
    { title: "约篇金额", dataIndex: "quote_amount", width: 100, render: money },
    { title: "发布量", dataIndex: "publish_count", width: 80 },
    { title: "合作金额", dataIndex: "cooperation_amount", width: 100, render: money },
    { title: "取消量", dataIndex: "cancel_count", width: 80 },
    { title: "超时量", dataIndex: "overdue_count", width: 80 },
    { title: "点赞量", dataIndex: "like_count", width: 80 },
    { title: "发布率", dataIndex: "publish_rate", width: 90, render: pct },
    { title: "超时率", dataIndex: "overdue_rate", width: 90, render: pct },
    { title: "点赞成本", dataIndex: "cpl", width: 100, render: money },
  ];

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card>
        <Space style={{ marginBottom: 16 }}>
          <span>时间范围：</span>
          <Select
            value={preset}
            style={{ width: 140 }}
            options={PRESETS}
            onChange={(v) => setPreset(v)}
          />
        </Space>
        <Row gutter={16}>
          <Col span={4}>
            <Statistic title="约篇量" value={summary?.quote_count ?? 0} />
          </Col>
          <Col span={4}>
            <Statistic title="约篇金额" value={summary?.quote_amount ?? 0} prefix="¥" />
          </Col>
          <Col span={4}>
            <Statistic title="发布量" value={summary?.publish_count ?? 0} />
          </Col>
          <Col span={4}>
            <Statistic
              title="发布率"
              value={summary?.publish_rate ? Number(summary.publish_rate) * 100 : 0}
              precision={1}
              suffix="%"
            />
          </Col>
          <Col span={4}>
            <Statistic title="点赞量" value={summary?.like_count ?? 0} />
          </Col>
          <Col span={4}>
            <Statistic title="取消量" value={summary?.cancel_count ?? 0} />
          </Col>
        </Row>
      </Card>

      <Card
        title={
          <Typography.Title level={5} style={{ margin: 0 }}>
            发文进度表 — 商品明细
          </Typography.Title>
        }
        extra={
          <Input.Search
            placeholder="搜索款号/品名"
            allowClear
            style={{ width: 220 }}
            onSearch={(v) => {
              setKeyword(v || undefined);
              setPage(1);
            }}
          />
        }
      >
        <Table
          rowKey="style_id"
          size="small"
          loading={isLoading}
          columns={columns}
          dataSource={cards?.items ?? []}
          scroll={{ x: 1200 }}
          pagination={{
            current: cards?.page ?? 1,
            pageSize: cards?.page_size ?? 12,
            total: cards?.total ?? 0,
            showTotal: (t) => `共 ${t} 款`,
            onChange: (p) => setPage(p),
          }}
        />
      </Card>
    </Space>
  );
}
