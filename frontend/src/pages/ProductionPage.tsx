import { useState } from "react";
import { Card, Select, Space, Switch, Table, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { getProduction } from "@/features/report/api";
import type { ProductionRow } from "@/features/report/types";

const PRESETS = [
  { label: "近7天", value: "last_7d" },
  { label: "近30天", value: "last_30d" },
  { label: "本月", value: "this_month" },
  { label: "上月", value: "last_month" },
];
const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const pct = (v: string | null) =>
  v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`;

export function ProductionPage() {
  const [preset, setPreset] = useState("last_30d");
  const [excludeBrushing, setExcludeBrushing] = useState(true);

  const { data, isLoading } = useQuery({
    queryKey: ["production", preset, excludeBrushing],
    queryFn: () =>
      getProduction({ preset, exclude_brushing: excludeBrushing }),
  });

  // 列对齐 final.xlsx「投产报表」核心派生指标
  const baseColumns: ColumnsType<ProductionRow> = [
    { title: "款号", dataIndex: "style_code", width: 120, fixed: "left" },
    { title: "款名", dataIndex: "style_name", width: 150 },
    { title: "支付金额", dataIndex: "pay_amount", width: 110, render: money },
    { title: "退款金额", dataIndex: "refund_amount", width: 110, render: money },
    { title: "退货退款率", dataIndex: "return_rate", width: 110, render: pct },
    { title: "待确认收货金额", dataIndex: "confirmed_amount", width: 130, render: money },
    { title: "推广花费", dataIndex: "promo_cost", width: 110, render: money },
    { title: "站内投放", dataIndex: "ad_spend", width: 110, render: money },
    { title: "推广总花费", dataIndex: "total_spend", width: 120, render: money },
    { title: "总加购数", dataIndex: "add_cart_count", width: 100 },
    { title: "加购成本", dataIndex: "add_cart_cost", width: 110, render: money },
    { title: "净投产比", dataIndex: "net_roi", width: 100, render: (v) => (v == null ? "—" : v) },
    { title: "推广单件成交成本", dataIndex: "unit_deal_cost", width: 150, render: money },
  ];

  // 动态展开千牛/站内导入按款式汇总的其余指标（对齐 final.xlsx 投产报表全列）
  const extraKeys = Array.from(
    new Set(
      (data?.items ?? []).flatMap((r) => Object.keys(r.extra ?? {})),
    ),
  );
  const extraColumns: ColumnsType<ProductionRow> = extraKeys.map((k) => ({
    title: k,
    key: `extra_${k}`,
    width: 130,
    render: (_: unknown, row: ProductionRow) => {
      const v = (row.extra ?? {})[k];
      return v == null || v === "" ? "—" : String(v);
    },
  }));

  const columns = [...baseColumns, ...extraColumns];
  const scrollX = 1500 + extraColumns.length * 130;

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          投产报表
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
        <span style={{ marginLeft: 12 }}>剔除刷单：</span>
        <Switch checked={excludeBrushing} onChange={setExcludeBrushing} />
      </Space>
      <Table
        rowKey="style_id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: scrollX }}
        pagination={false}
      />
    </Card>
  );
}
