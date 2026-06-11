import { useState } from "react";
import { Card, Select, Space, Table, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { getStoreDaily } from "@/features/report/api";
import type { StoreDailyRow } from "@/features/report/types";

const PRESETS = [
  { label: "近7天", value: "last_7d" },
  { label: "近30天", value: "last_30d" },
  { label: "本月", value: "this_month" },
  { label: "上月", value: "last_month" },
];
const money = (v: string | null) => (v == null ? "—" : `¥${v}`);

export function StoreDailyPage() {
  const [preset, setPreset] = useState("last_30d");
  const { data, isLoading } = useQuery({
    queryKey: ["store-daily", preset],
    queryFn: () => getStoreDaily({ preset }),
  });

  // 列对齐 final.xlsx「店铺数据」(千牛汇总 + 手动投放)
  const columns: ColumnsType<StoreDailyRow> = [
    { title: "日期", dataIndex: "date", width: 120 },
    { title: "访客数", dataIndex: "visitors", width: 100 },
    { title: "支付金额", dataIndex: "pay_amount", width: 120, render: money },
    { title: "支付订单数", dataIndex: "pay_orders", width: 110 },
    { title: "全站推消耗", dataIndex: "ad_spend_total", width: 120, render: money },
    { title: "直通车消耗", dataIndex: "zhitongche_spend", width: 120, render: money },
    { title: "引力魔方消耗", dataIndex: "yinli_spend", width: 130, render: money },
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
      </Space>
      <Table
        rowKey="date"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        pagination={false}
      />
    </Card>
  );
}
