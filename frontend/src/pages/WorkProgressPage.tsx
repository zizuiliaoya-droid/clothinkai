import { useState } from "react";
import { Button, Card, Select, Space, Table, Typography, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { useSearchParams } from "react-router-dom";
import {
  ReportCardTitle,
  ReportFreshness,
} from "@/components/ReportFreshness/ReportFreshness";
import {
  ReportTimeRangeFilter,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";
import { exportReport, getReportSeasonOptions, getWorkProgress } from "@/features/report/api";
import { DrillPromotionModal } from "@/features/report/components/DrillPromotionModal";
import { ReportForbidden } from "@/features/report/components/ReportForbidden";
import type { DrillPromotionTarget } from "@/features/report/drill";
import type { PrWorkProgress } from "@/features/report/types";
import {
  fromDateRange,
  toDateRange,
  useReportFilters,
} from "@/features/report/useReportFilters";
import { useSummaryFreshness } from "@/features/report/useSummaryFreshness";
import {
  columnsWidth,
  workProgressMetricColumns,
} from "@/features/report/workProgress/workProgressColumns";
import {
  WORK_PROGRESS_FILTER_DEFAULTS,
  WORK_PROGRESS_FILTER_SCHEMA,
  seasonParam,
} from "@/features/report/workProgress/workProgressFilters";
import { isForbiddenError } from "@/features/warehouse/shipmentForm";
import { extractErrorMessage } from "@/services/apiClient";
import {
  canExportReport,
  useReportCapabilities,
} from "@/features/report/useReportCapabilities";

/** 次要文字：slate-600，白底约 7.6:1。 */
const MUTED = "#475569";

export function WorkProgressPage() {
  const { filters, ready, setFilters } = useReportFilters(
    "pr_work_progress",
    WORK_PROGRESS_FILTER_SCHEMA,
    WORK_PROGRESS_FILTER_DEFAULTS
  );
  const { preset, season } = filters;
  const seasons = seasonParam(season);
  const range = toDateRange(filters.date_from, filters.date_to);
  const { dateFrom, dateTo, enabled } = useReportTimeRange(preset, range);
  // 季节写进记忆才算锁定：URL 带来的季节只本次生效（手动改任一筛选后 URL 键清掉、连同季节写进记忆）
  const [searchParams] = useSearchParams();
  const seasonLocked = season.length > 0 && !searchParams.has("season");

  // 季节选项：投产或工作进度（含只看本人）读权限即可（7e R5 放宽）
  const { data: seasonOptionList } = useQuery({
    queryKey: ["reports", "season-options"],
    queryFn: getReportSeasonOptions,
  });
  const seasonOptions = (seasonOptionList ?? []).map((s) => ({ label: s, value: s }));

  const { data, isLoading, error } = useQuery({
    queryKey: ["work-progress", preset, dateFrom, dateTo, season],
    // 等偏好回填完再查，避免先用默认区间查一次、回填后又查一次。
    enabled: enabled && ready,
    // 403 重试没有意义，直接显示无权限
    retry: (count, err) => !isForbiddenError(err) && count < 1,
    queryFn: () =>
      getWorkProgress({ preset, date_from: dateFrom, date_to: dateTo, season: seasons }),
  });
  // 导出、数据新鲜度、下钻都用这同一个区间。下钻要具体日期：用新鲜度接口里服务端解析的起止
  // （与 <ReportFreshness> 同一个查询，不多发请求；预设模式下前端手里没有日期）
  const { data: freshness } = useSummaryFreshness(preset, dateFrom, dateTo, enabled && ready);
  const [drillTarget, setDrillTarget] = useState<DrillPromotionTarget | null>(null);
  const openDrill = (row: PrWorkProgress) => {
    if (!freshness) return;
    setDrillTarget({
      title: row.pr_name,
      prId: row.pr_id,
      dateFrom: freshness.date_from,
      dateTo: freshness.date_to,
      season: seasons,
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
        season: seasons,
      }),
    onSuccess: (filename) => message.success(`已导出 ${filename}`),
    onError: (err) => message.error(extractErrorMessage(err, "导出失败")),
  });

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
    ...workProgressMetricColumns<PrWorkProgress>(),
  ];

  // 菜单按 capabilities 已隐藏；直接输入 URL 进来、接口 403 时给明确提示，不显示空表
  if (isForbiddenError(error)) {
    return <ReportForbidden name="工作进度表" />;
  }

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
        <span style={{ marginLeft: 12 }}>季节/系列：</span>
        <Select
          aria-label="季节或系列"
          mode="multiple"
          value={season}
          style={{ minWidth: 180, maxWidth: 320 }}
          placeholder="全部"
          allowClear
          maxTagCount="responsive"
          options={seasonOptions}
          onChange={(v: string[]) => setFilters({ season: v })}
        />
        {seasonLocked && (
          <Typography.Text style={{ color: MUTED }}>季节已锁定，下次打开保持</Typography.Text>
        )}
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
        scroll={{ x: columnsWidth(columns) }}
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
