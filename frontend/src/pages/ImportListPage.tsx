import {
  Alert,
  Button,
  Card,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  downloadImportErrors,
  listImportBatches,
  retryImportBatch,
} from "@/features/import/api";
import type { ImportBatch } from "@/features/import/types";
import { extractErrorMessage } from "@/services/apiClient";

const statusColor: Record<string, string> = {
  processing: "blue",
  completed: "green",
  partial: "gold",
  failed: "red",
};

/**
 * 导入记录（只读监控）。上传入口已下放到各业务模块页面（商品成本表/千牛数据/
 * 站内推广/博主库/站外推广/财务结款的「导入」按钮）。此处统一查看批次状态、
 * 重试失败批次、下载失败明细。
 */
export function ImportListPage() {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["import-batches"],
    queryFn: () => listImportBatches({ page: 1, page_size: 50 }),
  });

  const retryMutation = useMutation({
    mutationFn: (id: string) => retryImportBatch(id),
    onSuccess: () => {
      message.success("已触发重试");
      void qc.invalidateQueries({ queryKey: ["import-batches"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  async function handleDownload(id: string) {
    try {
      const blob = await downloadImportErrors(id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `import-errors-${id}.csv`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      message.error(extractErrorMessage(err));
    }
  }

  const columns: ColumnsType<ImportBatch> = [
    { title: "来源", dataIndex: "source", width: 120 },
    { title: "文件名", dataIndex: "original_filename" },
    {
      title: "状态",
      dataIndex: "status",
      width: 100,
      render: (v: string) => <Tag color={statusColor[v]}>{v}</Tag>,
    },
    { title: "总行数", dataIndex: "total_rows", width: 90 },
    { title: "成功", dataIndex: "imported", width: 80 },
    { title: "失败", dataIndex: "failed", width: 80 },
    { title: "重试次数", dataIndex: "retry_count", width: 90 },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 170,
      render: (v) => (v ? v.replace("T", " ").slice(0, 19) : "—"),
    },
    {
      title: "操作",
      width: 160,
      render: (_, r) => (
        <Space>
          {(r.status === "partial" || r.status === "failed") && (
            <Button
              type="link"
              size="small"
              onClick={() => retryMutation.mutate(r.id)}
            >
              重试
            </Button>
          )}
          {r.failed > 0 && (
            <Button type="link" size="small" onClick={() => handleDownload(r.id)}>
              失败明细
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          导入记录
        </Typography.Title>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="上传入口已下放到各业务模块"
        description="请到对应模块页面（商品成本表 / 千牛数据 / 站内推广 / 博主库 / 站外推广 / 财务结款）点击「导入」按钮上传 Excel/CSV。本页用于查看导入批次状态、重试失败批次、下载失败明细。"
      />
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={data?.items ?? []}
        scroll={{ x: 1000 }}
        pagination={false}
      />
    </Card>
  );
}
