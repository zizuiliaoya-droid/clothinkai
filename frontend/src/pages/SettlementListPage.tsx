import { useState } from "react";
import { Card, Select, Space, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { listSettlements } from "@/features/finance/api";
import type {
  Settlement,
  SettlementListFilters,
  SettlementStatus,
} from "@/features/finance/types";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);
const STATUS: SettlementStatus[] = [
  "待核查",
  "待付款",
  "待财务付款",
  "已付款",
  "已驳回",
];
const statusColor: Record<string, string> = {
  待核查: "orange",
  待付款: "blue",
  待财务付款: "cyan",
  已付款: "green",
  已驳回: "red",
};

/**
 * 财务结款。列对齐 final.xlsx「站外结款表」：
 * 月份|日期|大类|项目|款式编码|款式|金额|寄/送|博主名|付款金额|付款日期|衣服成本|总成本|付款图片|备注
 * 注：款式编码/款式/博主名/寄送/衣服成本 等反范式列后端 list 暂未 join，显示「—」。
 */
export function SettlementListPage() {
  const [filters, setFilters] = useState<SettlementListFilters>({
    page: 1,
    page_size: 20,
  });

  const { data, isLoading } = useQuery({
    queryKey: ["settlements", filters],
    queryFn: () => listSettlements(filters),
  });

  const columns: ColumnsType<Settlement> = [
    {
      title: "月份",
      width: 90,
      render: (_, r) => (r.payment_date || r.created_at || "").slice(0, 7) || "—",
    },
    {
      title: "日期",
      width: 110,
      render: (_, r) => (r.payment_date || r.created_at || "").slice(0, 10) || "—",
    },
    { title: "大类", width: 90, render: () => "站外推广" },
    { title: "项目", width: 80, render: () => "佣金" },
    { title: "款式编码", dataIndex: "style_code", width: 120, render: (v) => v || "—" },
    { title: "款式", dataIndex: "style_name", width: 140, render: (v) => v || "—" },
    { title: "博主名", dataIndex: "blogger_nickname", width: 120, render: (v) => v || "—" },
    { title: "结算单号", dataIndex: "settlement_no", width: 150 },
    { title: "金额", dataIndex: "amount", width: 100, render: money },
    { title: "付款金额", dataIndex: "payment_amount", width: 110, render: money },
    {
      title: "付款日期",
      dataIndex: "payment_date",
      width: 110,
      render: (v) => v || "—",
    },
    { title: "总成本", dataIndex: "total_amount", width: 100, render: money },
    {
      title: "付款图片",
      width: 90,
      render: (_, r) =>
        r.payment_proof_signed_url ? (
          <a href={r.payment_proof_signed_url} target="_blank" rel="noreferrer">
            查看
          </a>
        ) : (
          "—"
        ),
    },
    {
      title: "结算状态",
      dataIndex: "settlement_status",
      width: 110,
      fixed: "right",
      render: (v: string) => <Tag color={statusColor[v]}>{v}</Tag>,
    },
    { title: "备注", dataIndex: "remark", render: (v) => v || "—" },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          财务结款
        </Typography.Title>
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder="结算状态"
          allowClear
          style={{ width: 140 }}
          options={STATUS.map((s) => ({ label: s, value: s }))}
          onChange={(v) =>
            setFilters((f) => ({ ...f, settlement_status: v, page: 1 }))
          }
        />
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1500 }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 20,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (page, page_size) =>
            setFilters((f) => ({ ...f, page, page_size })),
        }}
      />
    </Card>
  );
}
