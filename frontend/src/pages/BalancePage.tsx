import { Card, Table, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { listBalanceRecords } from "@/features/finance/api";
import type { BalanceRecord } from "@/features/finance/types";

const money = (v: string | null) => (v == null ? "—" : `¥${v}`);

export function BalancePage() {
  const { data, isLoading } = useQuery({
    queryKey: ["balance-records"],
    queryFn: () => listBalanceRecords(),
  });

  // 列对齐 final.xlsx「余额核对」：日期|充值收入|推广支出|刷/拍单支出|余额|余额截图|备注
  const isBrushTao = (t: string) => t.includes("刷") || t.includes("拍");
  const columns: ColumnsType<BalanceRecord> = [
    { title: "日期", dataIndex: "record_date", width: 120 },
    {
      title: "充值收入",
      dataIndex: "income",
      width: 120,
      render: money,
    },
    {
      title: "推广支出",
      width: 120,
      render: (_, r) => (r.expense != null && !isBrushTao(r.record_type) ? `¥${r.expense}` : "—"),
    },
    {
      title: "刷/拍单支出",
      width: 130,
      render: (_, r) => (r.expense != null && isBrushTao(r.record_type) ? `¥${r.expense}` : "—"),
    },
    { title: "余额", dataIndex: "balance_after", width: 120, render: money },
    { title: "余额截图", width: 100, render: () => "—" },
    { title: "备注", dataIndex: "remark", render: (v) => v || "—" },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          余额核对
        </Typography.Title>
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        pagination={false}
      />
    </Card>
  );
}
