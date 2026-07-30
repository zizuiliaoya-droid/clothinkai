import { useMemo, useState } from "react";
import { Card, Select, Space, Table, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import dayjs from "dayjs";
import { getStoreDaily } from "@/features/report/api";
import type { StoreDailyRow, TimeGranularity, TimePreset } from "@/features/report/types";
import {
  ReportTimeRangeFilter,
  type ReportDateRange,
  useReportTimeRange,
} from "@/components/ReportTimeRangeFilter/ReportTimeRangeFilter";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const GRANULARITY: Array<{ label: string; value: TimeGranularity }> = [
  { label: "按日", value: "day" },
  { label: "按周", value: "week" },
  { label: "按月", value: "month" },
  { label: "按年", value: "year" },
];

// 周起始（周一）YYYY-MM-DD；仅做日历日期运算，不转 UTC，避免时区偏移。
function weekKey(value: string): string {
  const current = dayjs(value);
  const daysFromMonday = (current.day() + 6) % 7;
  return current.subtract(daysFromMonday, "day").format("YYYY-MM-DD");
}

function groupKey(value: string, granularity: TimeGranularity): string {
  if (granularity === "year") return value.slice(0, 4);
  if (granularity === "month") return value.slice(0, 7);
  return weekKey(value);
}

export function StoreDailyPage() {
  const [preset, setPreset] = useState<TimePreset>("last_30d");
  const [range, setRange] = useState<ReportDateRange>(null);
  const [granularity, setGranularity] = useState<TimeGranularity>("day");
  const { dateFrom: df, dateTo: dt, enabled } = useReportTimeRange(preset, range);
  const { data: raw, isLoading } = useQuery({
    queryKey: ["store-daily", preset, df, dt],
    enabled,
    queryFn: () => getStoreDaily({ preset, date_from: df, date_to: dt }),
  });

  // 按日/周/月/年聚合；保持既有可转数字字段求和口径。
  const data = useMemo<StoreDailyRow[]>(() => {
    const rows = raw ?? [];
    if (granularity === "day") return rows;
    const groups = new Map<string, StoreDailyRow>();
    for (const r of rows) {
      const key = groupKey(String(r.date), granularity);
      const g = groups.get(key);
      if (!g) {
        groups.set(key, { ...r, date: key, extra: { ...(r.extra ?? {}) } });
        continue;
      }
      for (const [k, v] of Object.entries(r)) {
        if (k === "date" || k === "extra") continue;
        const n = Number(v);
        if (!Number.isNaN(n) && v != null && v !== "") {
          (g as Record<string, unknown>)[k] = Number((g as Record<string, unknown>)[k] ?? 0) + n;
        }
      }
      const ge = (g.extra ?? {}) as Record<string, unknown>;
      for (const [k, v] of Object.entries(r.extra ?? {})) {
        const n = Number(v);
        if (!Number.isNaN(n) && v != null && v !== "") {
          ge[k] = Number(ge[k] ?? 0) + n;
        }
      }
      g.extra = ge;
    }
    return Array.from(groups.values()).sort((a, b) =>
      String(a.date) < String(b.date) ? 1 : -1
    );
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
    { title: "日期", dataIndex: "date", width: 120, fixed: "left" },
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
        <Typography.Title level={4} style={{ margin: 0 }}>
          店铺数据
        </Typography.Title>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <ReportTimeRangeFilter
          preset={preset}
          onPresetChange={setPreset}
          range={range}
          onRangeChange={setRange}
        />
        <span style={{ marginLeft: 12 }}>统计单位：</span>
        <Select<TimeGranularity>
          aria-label="店铺数据统计单位"
          value={granularity}
          style={{ width: 110 }}
          options={GRANULARITY}
          onChange={setGranularity}
        />
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
