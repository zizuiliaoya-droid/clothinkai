import { useState } from "react";
import { Button, Card, DatePicker, Space, Table, Typography, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { exportReport, getWorkProgress } from "@/features/report/api";
import type { PrWorkProgress } from "@/features/report/types";
import { extractErrorMessage } from "@/services/apiClient";

const pct = (v: string | null) =>
  v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`;

export function WorkProgressPage() {
  const [month, setMonth] = useState(dayjs().format("YYYY-MM"));

  const { data, isLoading } = useQuery({
    queryKey: ["work-progress", month],
    queryFn: () => getWorkProgress(month),
  });
  const exportMutation = useMutation({
    mutationFn: () => {
      const selected = dayjs(`${month}-01`);
      return exportReport("work-progress", {
        preset: "custom",
        date_from: selected.startOf("month").format("YYYY-MM-DD"),
        date_to: selected.endOf("month").format("YYYY-MM-DD"),
      });
    },
    onSuccess: (filename) => message.success(`已导出 ${filename}`),
    onError: (error) => message.error(extractErrorMessage(error, "导出失败")),
  });

  // 列对齐 final.xlsx「工作进度表」(20列)
  const columns: ColumnsType<PrWorkProgress> = [
    { title: "负责PR", dataIndex: "pr_name", width: 100, fixed: "left" },
    { title: "约篇件数", dataIndex: "quote_count", width: 90 },
    { title: "档期内", dataIndex: "in_schedule_count", width: 80 },
    { title: "催发", dataIndex: "urge_count", width: 70 },
    { title: "重要催发", dataIndex: "important_urge_count", width: 90 },
    { title: "超时", dataIndex: "overdue_count", width: 70 },
    { title: "已发布", dataIndex: "publish_count", width: 80 },
    {
      title: "已填写点赞量数量",
      dataIndex: "info_complete_count",
      width: 140,
    },
    { title: "信息完整度", dataIndex: "info_complete_rate", width: 100, render: pct },
    { title: "已取消", dataIndex: "cancel_count", width: 80 },
    { title: "应召回", dataIndex: "recall_due_count", width: 80 },
    { title: "召回成功", dataIndex: "recall_success_count", width: 90 },
    { title: "召回完成率", dataIndex: "recall_complete_rate", width: 100, render: pct },
    { title: "超时率", dataIndex: "overdue_rate", width: 90, render: pct },
    { title: "月度完成率", dataIndex: "month_complete_rate", width: 100, render: pct },
    { title: "爆文数", dataIndex: "hit_count", width: 80 },
    { title: "爆文率", dataIndex: "hit_rate", width: 90, render: pct },
    { title: "点赞数", dataIndex: "like_count", width: 90 },
    {
      title: "成本(含衣服)",
      dataIndex: "cost",
      width: 110,
      render: (v: string) => (v == null ? "—" : `¥${v}`),
    },
    {
      title: "CPL(元/赞)",
      dataIndex: "cpl",
      width: 100,
      render: (v: string | null) => (v == null ? "—" : `¥${v}`),
    },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          工作进度表
        </Typography.Title>
      }
    >
      <Space style={{ marginBottom: 16 }}>
        <span>月份：</span>
        <DatePicker
          picker="month"
          aria-label="工作进度月份"
          value={dayjs(month)}
          onChange={(d) => d && setMonth(d.format("YYYY-MM"))}
          allowClear={false}
        />
        <Button
          icon={<DownloadOutlined />}
          loading={exportMutation.isPending}
          onClick={() => exportMutation.mutate()}
        >
          导出
        </Button>
      </Space>
      <Table
        rowKey={(r) => r.pr_id ?? r.pr_name}
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        scroll={{ x: 1840 }}
        pagination={false}
      />
    </Card>
  );
}
