import { useState } from "react";
import { Button, Card, Space, Table, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  ReportCardTitle,
  ReportFreshness,
} from "@/components/ReportFreshness/ReportFreshness";
import {
  ReportTimeRangeFilter,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";
import { exportReport, getWorkProgress } from "@/features/report/api";
import { DrillPromotionModal } from "@/features/report/components/DrillPromotionModal";
import type { DrillPromotionTarget } from "@/features/report/drill";
import { TIME_PRESETS, type ReportFilterSchema } from "@/features/report/reportFilters";
import type { PrWorkProgress, TimePreset } from "@/features/report/types";
import {
  fromDateRange,
  toDateRange,
  useReportFilters,
} from "@/features/report/useReportFilters";
import { useSummaryFreshness } from "@/features/report/useSummaryFreshness";
import { extractErrorMessage } from "@/services/apiClient";
import {
  canExportReport,
  useReportCapabilities,
} from "@/features/report/useReportCapabilities";
const pct = (v: string | null) =>
  v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`;
/**
 * 筛选状态（7e R1）：区间 = preset + 起止日期，记忆里连日期一起记；
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
  const range = toDateRange(filters.date_from, filters.date_to);
  const { dateFrom, dateTo, enabled } = useReportTimeRange(preset, range);
  const { data, isLoading } = useQuery({
    queryKey: ["work-progress", preset, dateFrom, dateTo],
    // 等偏好回填完再查，避免先用默认区间查一次、回填后又查一次。
    enabled: enabled && ready,
    queryFn: () => getWorkProgress({ preset, date_from: dateFrom, date_to: dateTo }),
  });
  // 导出、数据新鲜度、下钻都用这同一个区间。下钻要具体日期：用新鲜度接口里服务端解析的起止
  // （与 <ReportFreshness> 同一个查询，不多发请求；预设模式下前端手里没有日期）
  const { data: freshness } = useSummaryFreshness(preset, dateFrom, dateTo, enabled && ready);
  const [drillTarget, setDrillTarget] = useState<DrillPromotionTarget | null>(null);
  const openDrill = (row: PrWorkProgress) => {
    if (!freshness) return;
    setDrillTarget({
      prId: row.pr_id,
      prName: row.pr_name,
      dateFrom: freshness.date_from,
      dateTo: freshness.date_to,
    });
  };
  // 导出按钮按 capabilities 出现（持 report.export:read 且能读这张表）；能力没回来前不显示
  const { data: reportCaps } = useReportCapabilities();
  const canExport = canExportReport(reportCaps, "work-progress");
  const ownOnly = reportCaps?.work_progress === "own";
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
    {
      title: "负责PR",
      dataIndex: "pr_name",
      width: 100,
      fixed: "left",
      // 点 PR 名（或整行）看该 PR 区间内的推广单；「未分配」行同样可点
      render: (name: string, row) =>
        freshness ? (
          <Button
            type="link"
            size="small"
            style={{ padding: 0, height: "auto" }}
            aria-label={`查看 ${name} 的推广单`}
            onClick={(e) => {
              e.stopPropagation();
              openDrill(row);
            }}
          >
            {name}
          </Button>
        ) : (
          name
        ),
    },
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
      render: (v: string | null) => (v == null ? "—" : `¥${v}`),
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
          title={ownOnly ? "工作进度表（只看本人）" : "工作进度表"}
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
      <Space style={{ marginBottom: 16 }} wrap>
        <ReportTimeRangeFilter
          preset={preset}
          onPresetChange={(v) => setFilters({ preset: v })}
          range={range}
          onRangeChange={(v) => setFilters(fromDateRange(v))}
        />
        {canExport && (
          <Button
            icon={<DownloadOutlined />}
            loading={exportMutation.isPending}
            disabled={!enabled}
            onClick={() => exportMutation.mutate()}
          >
            导出
          </Button>
        )}
      </Space>
      <Table
        rowKey={(r) => r.pr_id ?? r.pr_name}
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        scroll={{ x: 1840 }}
        pagination={false}
        // 整行可点（鼠标）；键盘走第一列的按钮（Tab 到 PR 名，Enter / 空格打开）
        onRow={(row) =>
          freshness
            ? {
                onClick: () => openDrill(row),
                style: { cursor: "pointer" },
              }
            : {}
        }
      />
      <DrillPromotionModal target={drillTarget} onClose={() => setDrillTarget(null)} />
    </Card>
  );
}