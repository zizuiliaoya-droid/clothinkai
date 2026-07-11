import { useMemo, useState } from "react";
import { Card, DatePicker, Select, Space, Table, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import type { Dayjs } from "dayjs";
import { getStoreDaily } from "@/features/report/api";
import type { StoreDailyRow } from "@/features/report/types";

const PRESETS = [
  { label: "近7天", value: "last_7d" },
  { label: "近30天", value: "last_30d" },
  { label: "本月", value: "this_month" },
  { label: "上月", value: "last_month" },
  { label: "自定义", value: "custom" },
];
const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const GRANULARITY = [
  { label: "按日", value: "day" },
  { label: "按周", value: "week" },
  { label: "按月", value: "month" },
];

// 周起始（周一）YYYY-MM-DD
function weekKey(d: string): string {
  const dt = new Date(d + "T00:00:00");
  const off = (dt.getDay() + 6) % 7; // 周一=0
  dt.setDate(dt.getDate() - off);
  return dt.toISOString().slice(0, 10);
}

export function StoreDailyPage() {
  const [preset, setPreset] = useState("last_30d");
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const [granularity, setGranularity] = useState("day");
  const isCustom = preset === "custom";
  const df = isCustom && range ? range[0].format("YYYY-MM-DD") : undefined;
  const dt = isCustom && range ? range[1].format("YYYY-MM-DD") : undefined;
  const enabled = !isCustom || (!!df && !!dt);
  const { data: raw, isLoading } = useQuery({
    queryKey: ["store-daily", preset, df, dt],
    enabled,
    queryFn: () => getStoreDaily({ preset, date_from: df, date_to: dt }),
  });

  // §8：按 日/周/月 聚合（对数值字段求和；日粒度不聚合）
  const data = useMemo<StoreDailyRow[]>(() => {
    const rows = raw ?? [];
    if (granularity === "day") return rows;
    const groups = new Map<string, StoreDailyRow>();
    for (const r of rows) {
      const key =
        granularity === "month"
          ? String(r.date).slice(0, 7)
          : weekKey(String(r.date));
      const g = groups.get(key);
      if (!g) {
        groups.set(key, { ...r, date: key });
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
      <Space style={{ marginBottom: 16 }}>
        <span>时间范围：</span>
        <Select
          value={preset}
          style={{ width: 140 }}
          options={PRESETS}
          onChange={setPreset}
        />
        {isCustom ? (
          <DatePicker.RangePicker
            value={range as never}
            onChange={(v) => setRange(v as [Dayjs, Dayjs] | null)}
            allowClear
          />
        ) : null}
        <span style={{ marginLeft: 12 }}>统计单位：</span>
        <Select
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
