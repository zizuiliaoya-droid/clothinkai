import { Button, Card, DatePicker, Space, Table, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import {
  ReportCardTitle,
  ReportFreshness,
} from "@/components/ReportFreshness/ReportFreshness";
import { useReportTimeRange } from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";
import { exportReport, getWorkProgress } from "@/features/report/api";
import { TIME_PRESETS, type ReportFilterSchema } from "@/features/report/reportFilters";
import type { PrWorkProgress, TimePreset } from "@/features/report/types";
import { toDateRange, useReportFilters } from "@/features/report/useReportFilters";
import { extractErrorMessage } from "@/services/apiClient";

const pct = (v: string | null) =>
  v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`;

/**
 * 筛选状态（7e R1）：区间 = preset + 起止日期，记忆里连日期一起记（选过的月份下次还在）；
 * 旧记忆 `{month}` 不再认，回落默认本月。
 */
interface WorkProgressFilters {
  preset: TimePreset;
  date_from?: string;
  date_to?: string;
}

const WORK_PROGRESS_FILTER_SCHEMA: ReportFilterSchema<WorkProgressFilters> = {
  preset: { kind: "enum", values: TIME_PRESETS, url: true, memory: true },
  date_from: { kind: "date", url: true, memory: true },
  date_to: { kind: "date", url: true, memory: true },
};

const WORK_PROGRESS_FILTER_DEFAULTS: WorkProgressFilters = { preset: "this_month" };

export function WorkProgressPage() {
  const { filters, ready, setFilters } = useReportFilters(
    "pr_work_progress",
    WORK_PROGRESS_FILTER_SCHEMA,
    WORK_PROGRESS_FILTER_DEFAULTS
  );
  const { preset } = filters;
  const { dateFrom, dateTo, enabled } = useReportTimeRange(
    preset,
    toDateRange(filters.date_from, filters.date_to)
  );
  // 月份选择器：选某月 = 该月整月的自定义区间；上月 / 其他预设显示对应月份（区间筛选控件随后替换它）
  const pickerMonth =
    preset === "custom" && dateFrom
      ? dayjs(dateFrom)
      : preset === "last_month"
        ? dayjs().subtract(1, "month")
        : dayjs();

  const { data, isLoading } = useQuery({
    queryKey: ["work-progress", preset, dateFrom, dateTo],
    // 等偏好回填完再查，避免先用当月查一次、回填后又查一次。
    enabled: enabled && ready,
    queryFn: () => getWorkProgress({ preset, date_from: dateFrom, date_to: dateTo }),
  });
  // 导出与数据新鲜度都用这同一个区间
  const exportMutation = useMutation({
    mutationFn: () =>
      exportReport("work-progress", {
        preset,
        date_from: dateFrom,
        date_to: dateTo,
      }),
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
        <ReportCardTitle
          title="工作进度表"
          // 区间每天都有汇总覆盖才读汇总表，否则（如含还没到的日子）显示「实时数据」
          freshness={
            <ReportFreshness
              preset={preset}
              dateFrom={dateFrom}
              dateTo={dateTo}
              enabled={enabled && ready}
            />
          }
        />
      }
    >
      <Space style={{ marginBottom: 16 }}>
        <span>月份：</span>
        <DatePicker
          picker="month"
          aria-label="工作进度月份"
          value={pickerMonth}
          onChange={(d) =>
            d &&
            setFilters({
              preset: "custom",
              date_from: d.startOf("month").format("YYYY-MM-DD"),
              date_to: d.endOf("month").format("YYYY-MM-DD"),
            })
          }
          allowClear={false}
        />
        <Button
          icon={<DownloadOutlined />}
          loading={exportMutation.isPending}
          disabled={!enabled}
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
