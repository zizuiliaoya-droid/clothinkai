import { Card, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { listOrderAdjustments } from "@/features/finance/api";
import type { OrderAdjustment } from "@/features/finance/types";

interface Props {
  orderType: "拍单" | "刷单";
}

/**
 * 拍单 / 刷单（统一 order_adjustment）。
 * 列对齐 final.xlsx「拍单」：销售类型|拍单日期|订单号|博主ID/微信ID|款式|款号|金额|付款金额|付款日期
 * 刷单额外展示「ROI剔除」。款式/款号/付款字段后端 list 暂未 join，显示「—」。
 */
export function OrderAdjustmentPage({ orderType }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["order-adjustments", orderType],
    queryFn: () => listOrderAdjustments({ order_type: orderType, limit: 200 }),
  });

  const columns: ColumnsType<OrderAdjustment> = [
    { title: "销售类型", dataIndex: "order_type", width: 90 },
    { title: orderType === "拍单" ? "拍单日期" : "日期", dataIndex: "order_date", width: 120, render: (v) => v || "—" },
    { title: "订单号", dataIndex: "order_no", width: 160, render: (v) => v || "—" },
    { title: "博主ID/微信ID", dataIndex: "blogger_identifier", width: 140, render: (v) => v || "—" },
    { title: "款式", dataIndex: "style_name", width: 140, render: (v) => v || "—" },
    { title: "款号", dataIndex: "style_code", width: 120, render: (v) => v || "—" },
    {
      title: "金额",
      dataIndex: "amount",
      width: 110,
      render: (v: string) => (v == null ? "—" : `¥${v}`),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (v: string) => <Tag color={v === "已付款" ? "green" : "orange"}>{v}</Tag>,
    },
    ...(orderType === "刷单"
      ? [
          {
            title: "ROI剔除",
            dataIndex: "exclude_from_roi",
            width: 90,
            render: (v: boolean) => (v ? <Tag color="red">剔除</Tag> : "—"),
          } as ColumnsType<OrderAdjustment>[number],
        ]
      : []),
    { title: "备注", dataIndex: "remark", render: (v) => v || "—" },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          {orderType}
        </Typography.Title>
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data ?? []}
        scroll={{ x: 1000 }}
        pagination={false}
      />
    </Card>
  );
}
