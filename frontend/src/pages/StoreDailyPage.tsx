import { useMemo } from "react";
import { Button, Card, Select, Space, Table, message } from "antd";
import { DownloadOutlined } from "@ant-design/icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { exportReport, getStoreDaily } from "@/features/report/api";
import { TIME_PRESETS, type ReportFilterSchema } from "@/features/report/reportFilters";
import {
  fromDateRange,
  toDateRange,
  useReportFilters,
} from "@/features/report/useReportFilters";
import type { StoreDailyRow, TimeGranularity, TimePreset } from "@/features/report/types";
import { extractErrorMessage } from "@/services/apiClient";
import {
  canExportReport,
  useReportCapabilities,
} from "@/features/report/useReportCapabilities";
import {
  ReportCardTitle,
  ReportFreshness,
} from "@/components/ReportFreshness/ReportFreshness";
import {
  ReportTimeRangeFilter,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const GRANULARITY: Array<{ label: string; value: TimeGranularity }> = [
  { label: "按日", value: "day" },
  { label: "按周", value: "week" },
  { label: "按月", value: "month" },
  { label: "按年", value: "year" },
];

// 后端给的是桶首日（YYYY-MM-DD）：按月只显示年月、按年只显示年份，周与日原样。
function bucketLabel(value: string, granularity: TimeGranularity): string {
  if (granularity === "year") return value.slice(0, 4);
  if (granularity === "month") return value.slice(0, 7);
  return value;
}

/** 筛选状态：preset / granularity 记忆；日期区间只从 URL 带入、不记；granularity 不在 URL 白名单里，只走记忆。 */
interface StoreDailyFilters {
  preset: TimePreset;
  date_from?: string;
  date_to?: string;
  granularity: TimeGranularity;
}

const STORE_FILTER_SCHEMA: ReportFilterSchema<StoreDailyFilters> = {
  preset: { kind: "enum", values: TIME_PRESETS, url: true, memory: true },
  date_from: { kind: "date", url: true, memory: false },
  date_to: { kind: "date", url: true, memory: false },
  granularity: {
    kind: "enum",
    values: GRANULARITY.map((o) => o.value),
    url: false,
    memory: true,
  },
};

const STORE_FILTER_DEFAULTS: StoreDailyFilters = { preset: "last_30d", granularity: "day" };

export function StoreDailyPage() {
  const { filters, ready, setFilters } = useReportFilters(
    "store_daily",
    STORE_FILTER_SCHEMA,
    STORE_FILTER_DEFAULTS
  );
  const { preset, granularity } = filters;
  const range = toDateRange(filters.date_from, filters.date_to);
  const { dateFrom: df, dateTo: dt, enabled } = useReportTimeRange(preset, range);
  const { data: raw, isLoading } = useQuery({
    queryKey: ["store-daily", preset, df, dt, granularity],
    // 等偏好回填完再查，避免先用默认值查一次、回填后又查一次。
    enabled: enabled && ready,
    queryFn: () =>
      getStoreDaily({ preset, date_from: df, date_to: dt, granularity }),
  });
  // 导出按钮按 capabilities 出现（持 report.export:read 且能读这张表）；能力没回来前不显示
  const { data: reportCaps } = useReportCapabilities();
  const canExport = canExportReport(reportCaps, "store-daily");
  const exportMutation = useMutation({
    mutationFn: () =>
      exportReport("store-daily", {
        preset,
        date_from: df,
        date_to: dt,
        granularity,
      }),
    onSuccess: (filename) => message.success(`已导出 ${filename}`),
    onError: (error) => message.error(extractErrorMessage(error, "导出失败")),
  });

  // 周 / 月 / 年由后端分桶，extra 也在后端按规则聚合（比率、累计不相加），页面不再自己加。
  // 后端按桶升序；非「按日」时倒过来，保持原来「最新在上」。
  const data = useMemo<StoreDailyRow[]>(() => {
    const rows = raw ?? [];
    return granularity === "day" ? rows : [...rows].reverse();
  }, [raw, granularity]);

  // typed 列（核心）+ 动态展开千牛汇总 extra（对齐 final.xlsx 店铺数据 24 列）
  const extraColumns = useMemo<ColumnsType<StoreDailyRow>>(() => {
    const keys = new Set<string>();
    for (const r of data ?? []) {
      Object.keys(r.extra ?? {}).forEach((k) => keys.add(k));
    }
    return Array.from(keys).map((k) => ({
      title: k,
      key: `ex_${k}`,
      width: 130,
      render: (_: unknown, r: StoreDailyRow) => {
        const v = (r.extra ?? {})[k];
        return v == null || v === "" ? "—" : String(v);
      },
    }));
  }, [data]);

  const columns: ColumnsType<StoreDailyRow> = [
    {
      title: "日期",
      dataIndex: "date",
      width: 120,
      fixed: "left",
      render: (v: string) => bucketLabel(v, granularity),
    },
    { title: "访客数", dataIndex: "visitors", width: 100 },
    { title: "支付金额", dataIndex: "pay_amount", width: 120, render: money },
    { title: "支付订单数", dataIndex: "pay_orders", width: 110 },
    { title: "全站推消耗", dataIndex: "ad_spend_total", width: 120, render: money },
    { title: "直通车消耗", dataIndex: "zhitongche_spend", width: 120, render: money },
    { title: "引力魔方消耗", dataIndex: "yinli_spend", width: 130, render: money },
    ...extraColumns,
  ];

  return (
    <Card
      title={
        <ReportCardTitle
          title="店铺数据"
          freshness={
            <ReportFreshness
              preset={preset}
              dateFrom={df}
              dateTo={dt}
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
        <span style={{ marginLeft: 12 }}>统计单位：</span>
        <Select<TimeGranularity>
          aria-label="店铺数据统计单位"
          value={granularity}
          style={{ width: 110 }}
          options={GRANULARITY}
          onChange={(v) => setFilters({ granularity: v })}
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
        rowKey="date"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        scroll={{ x: Math.max(900, columns.length * 130) }}
        pagination={false}
      />
    </Card>
  );
}
