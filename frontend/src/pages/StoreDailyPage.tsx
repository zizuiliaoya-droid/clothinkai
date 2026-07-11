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

export function StoreDailyPage() {
  const [preset, setPreset] = useState("last_30d");
  const [range, setRange] = useState<[Dayjs, Dayjs] | null>(null);
  const isCustom = preset === "custom";
  const df = isCustom && range ? range[0].format("YYYY-MM-DD") : undefined;
  const dt = isCustom && range ? range[1].format("YYYY-MM-DD") : undefined;
  const enabled = !isCustom || (!!df && !!dt);
  const { data, isLoading } = useQuery({
    queryKey: ["store-daily", preset, df, dt],
    enabled,
    queryFn: () => getStoreDaily({ preset, date_from: df, date_to: dt }),
  });

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
