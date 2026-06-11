import { useMemo } from "react";
import { Card, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { listDesigns } from "@/features/design/api";
import type { DesignListItem } from "@/features/design/api";

interface Props {
  title: string;
  /** 该页关注的设计状态（空=全部）。 */
  statuses?: string[];
}

const statusColor: Record<string, string> = {
  设计中: "default",
  制版中: "blue",
  工艺录入: "cyan",
  待补全: "gold",
  待核价: "orange",
  已确认: "green",
  大货: "green",
  已取消: "red",
};

/**
 * 设计制版看板（表格版）。设计管理/制版管理/工艺管理/核价管理共用，按状态筛选。
 * 设计流程：设计中→制版中→工艺录入→待补全→待核价→大货
 */
export function DesignListPage({ title, statuses }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["designs"],
    queryFn: () => listDesigns(),
  });

  const rows = useMemo(() => {
    const all = (data?.groups ?? []).flatMap((g) => g.items);
    if (!statuses || statuses.length === 0) return all;
    return all.filter((r) => statuses.includes(r.design_status));
  }, [data, statuses]);

  const columns: ColumnsType<DesignListItem> = [
    { title: "款号", dataIndex: "style_code", width: 160 },
    { title: "款名", dataIndex: "style_name" },
    {
      title: "设计状态",
      dataIndex: "design_status",
      width: 120,
      render: (v: string) => <Tag color={statusColor[v] ?? "default"}>{v}</Tag>,
    },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={rows}
        pagination={{ pageSize: 20, showTotal: (t) => `共 ${t} 款` }}
      />
    </Card>
  );
}
