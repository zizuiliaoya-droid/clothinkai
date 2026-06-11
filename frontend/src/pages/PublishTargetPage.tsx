import { useState } from "react";
import { Card, DatePicker, Space, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { getTargets } from "@/features/report/api";
import type { TargetWithActual } from "@/features/report/types";

export function PublishTargetPage() {
  const [month, setMonth] = useState(dayjs().format("YYYY-MM"));

  const { data, isLoading } = useQuery({
    queryKey: ["targets", month],
    queryFn: () => getTargets(month),
  });

  // 列对齐 final.xlsx「爆款约篇数量」
  const columns: ColumnsType<TargetWithActual> = [
    { title: "负责PR", dataIndex: "pr_name", width: 110 },
    { title: "款号", dataIndex: "style_code", width: 120 },
    { title: "商品名称", dataIndex: "style_name" },
    { title: "统计月份", dataIndex: "period_month", width: 110 },
    { title: "最低约篇", dataIndex: "min_target", width: 100 },
    { title: "实际约篇", dataIndex: "actual_count", width: 100 },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (v: string) => (
        <Tag color={v.includes("达标") && !v.includes("未") ? "green" : "red"}>
          {v}
        </Tag>
      ),
    },
    {
      title: "缺口/超额",
      dataIndex: "gap",
      width: 100,
      render: (v: number) => (
        <span style={{ color: v >= 0 ? "#52c41a" : "#f5222d" }}>
          {v >= 0 ? `+${v}` : v}
        </span>
      ),
    },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          爆款约篇数量
        </Typography.Title>
      }
    >
      <Space style={{ marginBottom: 16 }}>
        <span>月份：</span>
        <DatePicker
          picker="month"
          value={dayjs(month)}
          onChange={(d) => d && setMonth(d.format("YYYY-MM"))}
          allowClear={false}
        />
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        pagination={false}
      />
    </Card>
  );
}
