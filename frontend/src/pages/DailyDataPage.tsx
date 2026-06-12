import { useMemo, useState } from "react";
import { Card, Table, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";

interface DailyPage<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

interface Props<T extends { id: string; extra?: Record<string, unknown> }> {
  title: string;
  /** typed 固定列。 */
  typedColumns: ColumnsType<T>;
  queryKey: string;
  fetchFn: (params: { page: number; page_size: number }) => Promise<DailyPage<T>>;
  /** 该模块在 final.xlsx 的总列数（用于页头标注）。 */
  totalCols: number;
}

/**
 * 千牛/站内推广日报通用表格。typed 列固定，其余原始列从 extra JSONB 动态展开，
 * 覆盖 final.xlsx 的完整列（38/72）。数据来源：导入入库。
 */
export function DailyDataPage<
  T extends { id: string; extra?: Record<string, unknown> }
>({ title, typedColumns, queryKey, fetchFn, totalCols }: Props<T>) {
  const [page, setPage] = useState(1);

  const { data, isLoading } = useQuery({
    queryKey: [queryKey, page],
    queryFn: () => fetchFn({ page, page_size: 50 }),
  });

  // 动态收集 extra 中出现的所有列名
  const extraColumns = useMemo<ColumnsType<T>>(() => {
    const keys = new Set<string>();
    for (const row of data?.items ?? []) {
      const e = row.extra ?? {};
      Object.keys(e).forEach((k) => keys.add(k));
    }
    return Array.from(keys).map((k) => ({
      title: k,
      key: `extra_${k}`,
      width: 140,
      render: (_: unknown, r: T) => {
        const v = (r.extra ?? {})[k];
        return v == null || v === "" ? "—" : String(v);
      },
    }));
  }, [data]);

  const columns = [...typedColumns, ...extraColumns];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          {title}
          <Typography.Text type="secondary" style={{ fontSize: 13, marginLeft: 12 }}>
            （final.xlsx {totalCols} 列；当前展示 {columns.length} 列）
          </Typography.Text>
        </Typography.Title>
      }
    >
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: Math.max(1200, columns.length * 140) }}
        pagination={{
          current: data?.page ?? 1,
          pageSize: data?.page_size ?? 50,
          total: data?.total ?? 0,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p) => setPage(p),
        }}
      />
    </Card>
  );
}
