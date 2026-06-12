import { useState } from "react";
import {
  Button,
  Card,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { UploadOutlined } from "@ant-design/icons";
import type { UploadProps } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  downloadImportErrors,
  listImportBatches,
  retryImportBatch,
  uploadImportFile,
} from "@/features/import/api";
import type { ImportBatch } from "@/features/import/types";
import { extractErrorMessage } from "@/services/apiClient";

const SOURCES = [
  { label: "千牛", value: "qianniu" },
  { label: "万相台", value: "wanxiangtai" },
  { label: "灰豚", value: "huitun" },
];
const statusColor: Record<string, string> = {
  processing: "blue",
  completed: "green",
  partial: "gold",
  failed: "red",
};

export function ImportListPage() {
  const qc = useQueryClient();
  const [source, setSource] = useState("qianniu");

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

  const uploadProps: UploadProps = {
    beforeUpload: async (file) => {
      try {
        await uploadImportFile(source, file as File);
        message.success("上传成功，已创建导入批次");
        void qc.invalidateQueries({ queryKey: ["import-batches"] });
      } catch (err) {
        message.error(extractErrorMessage(err));
      }
      return false; // 阻止 antd 默认上传
    },
    showUploadList: false,
    accept: ".csv,.xlsx,.xls",
  };

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
    { title: "来源", dataIndex: "source", width: 100 },
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
      title={<Typography.Title level={4} style={{ margin: 0 }}>数据导入</Typography.Title>}
      extra={
        <Space>
          <Select
            value={source}
            style={{ width: 120 }}
            options={SOURCES}
            onChange={setSource}
          />
          <Upload {...uploadProps}>
            <Button type="primary" icon={<UploadOutlined />}>
              上传文件
            </Button>
          </Upload>
        </Space>
      }
    >
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
