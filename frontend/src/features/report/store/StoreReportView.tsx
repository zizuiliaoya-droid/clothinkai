// 店铺日明细 / 周汇总 / 月度汇总（7e R4，设计 2.4）：三页共用的视图，pages/store/* 只传粒度。
//
// 后端按「ISO 周（自然月）∩ 请求区间」分桶，行上带实际起止；日表升序，周 / 月倒序（最新在上）。
// 日表标题旁显示整段「仅退款已录 x / y 天」，周 / 月在每行的仅退款率下面显示。
import { useMemo } from "react";
import { Button, Card, Space, Table, Typography, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { FilterPageCode } from "@/features/preference/api";
import { exportReport, getStoreReport } from "@/features/report/api";
import { TIME_PRESETS, type ReportFilterSchema } from "@/features/report/reportFilters";
import {
  fromDateRange,
  toDateRange,
  useReportFilters,
} from "@/features/report/useReportFilters";
import type { StoreGranularity, StoreReportRow, TimePreset } from "@/features/report/types";
import {
  canExportReport,
  useReportCapabilities,
} from "@/features/report/useReportCapabilities";
import { extractErrorMessage } from "@/services/apiClient";
import { isForbiddenError } from "@/features/warehouse/shipmentForm";
import { ReportForbidden } from "@/features/report/components/ReportForbidden";
import {
  ReportCardTitle,
  ReportFreshness,
} from "@/components/ReportFreshness/ReportFreshness";
import {
  ReportTimeRangeFilter,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";
import { recordedDaysLabel } from "./storeFormat";
import { buildStoreColumns, storeColumnsWidth } from "./storeColumns";

/** 筛选状态：preset 记忆；日期区间只从 URL 带入（下钻、书签）、不记。 */
interface StoreFilters {
  preset: TimePreset;
  date_from?: string;
  date_to?: string;
}

const STORE_FILTER_SCHEMA: ReportFilterSchema<StoreFilters> = {
  preset: { kind: "enum", values: TIME_PRESETS, url: true, memory: true },
  date_from: { kind: "date", url: true, memory: false },
  date_to: { kind: "date", url: true, memory: false },
};

const STORE_FILTER_DEFAULTS: StoreFilters = { preset: "last_30d" };

const PAGES: Record<StoreGranularity, { title: string; pageCode: FilterPageCode }> = {
  day: { title: "店铺日明细", pageCode: "store_daily" },
  week: { title: "店铺周汇总", pageCode: "store_weekly" },
  month: { title: "店铺月度汇总", pageCode: "store_monthly" },
};

const LABEL_STYLE = { color: "#475569", fontSize: 14, fontWeight: 400 } as const;

export function StoreReportView({ granularity }: { granularity: StoreGranularity }) {
  const page = PAGES[granularity];
  const { filters, ready, setFilters } = useReportFilters(
    page.pageCode,
    STORE_FILTER_SCHEMA,
    STORE_FILTER_DEFAULTS
  );
  const { preset } = filters;
  const range = toDateRange(filters.date_from, filters.date_to);
  const { dateFrom: df, dateTo: dt, enabled } = useReportTimeRange(preset, range);
  const { data: report, isLoading, error } = useQuery({
    // 前缀 store-daily：手动刷新汇总后 ReportFreshness 按它作废
    queryKey: ["store-daily", granularity, preset, df, dt],
    // 等偏好回填完再查，避免先用默认值查一次、回填后又查一次。
    enabled: enabled && ready,
    // 403 重试没有意义，直接显示无权限
    retry: (count, err) => !isForbiddenError(err) && count < 1,
    queryFn: () => getStoreReport(granularity, { preset, date_from: df, date_to: dt }),
  });
  // 导出按钮按 capabilities 出现（持 report.export:read 且能读这张表）；能力没回来前不显示
  const { data: reportCaps } = useReportCapabilities();
  const canExport = canExportReport(reportCaps, "store-daily");
  const exportMutation = useMutation({
    mutationFn: () =>
      exportReport("store-daily", { preset, date_from: df, date_to: dt, granularity }),
    onSuccess: (filename) => message.success(`已导出 ${filename}`),
    onError: (error) => message.error(extractErrorMessage(error, "导出失败")),
  });

  const rows = useMemo<StoreReportRow[]>(() => {
    const items = report?.items ?? [];
    return granularity === "day" ? items : [...items].reverse();
  }, [report, granularity]);
  const columns = useMemo(() => buildStoreColumns(granularity, rows), [granularity, rows]);

  // 菜单按 capabilities 已隐藏；直接输入 URL 进来、接口 403 时给明确提示，不显示空表
  if (isForbiddenError(error)) {
    return <ReportForbidden name={page.title} />;
  }

  return (
    <Card
      title={
        <ReportCardTitle
          title={page.title}
          freshness={
            <Space size={16} wrap>
              {granularity === "day" && report && (
                <Typography.Text style={LABEL_STYLE}>
                  仅退款{recordedDaysLabel(report.recorded_days, report.total_days)}
                </Typography.Text>
              )}
              <ReportFreshness
                preset={preset}
                dateFrom={df}
                dateTo={dt}
                enabled={enabled && ready}
              />
            </Space>
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
        rowKey="date_from"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={rows}
        scroll={{ x: storeColumnsWidth(columns) }}
        pagination={false}
      />
    </Card>
  );
}
