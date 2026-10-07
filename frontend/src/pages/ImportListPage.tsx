import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { useSearchParams } from "react-router-dom";
import { ImportResultModal } from "@/components/ImportResultModal/ImportResultModal";
import {
  downloadImportErrors,
  getImportAccess,
  listImportBatches,
  retryImportBatch,
} from "@/features/import/api";
import type { ImportBatch } from "@/features/import/types";
import { ConflictPanel } from "@/pages/imports/ConflictPanel";
import { extractErrorMessage } from "@/services/apiClient";

const statusColor: Record<string, string> = {
  processing: "blue",
  completed: "green",
  partial: "gold",
  failed: "red",
};

type TabKey = "batches" | "conflicts";

const SUCCESS_HELP = "商品资料、博主：新增或已覆盖的行；其他来源与原来相同";
const DASH = "—";

/**
 * 导入记录（只读监控 + 冲突处理）。上传入口已下放到各业务模块页面（商品成本表/千牛数据/
 * 站内推广/博主库/站外推广/财务结款的「导入」按钮）。
 *
 * 两个页签，与地址栏 ``?tab=conflicts&source=&batch_id=`` 同步（8a-6）：
 * - 导入批次：查看批次状态、重试失败批次、下载失败明细。现有列的列名与取值不变；新增的
 *   「仅补空」「重复已跳过」「冲突」「补空」四列只对重复规则可切换的来源（商品资料、博主）显示数值，
 *   其他来源一律「—」
 * - 冲突处理：按来源 / 批次 / 状态 / 字段筛选，单条或多选「用文件覆盖」/「保留系统值」，下载 CSV
 */
export function ImportListPage() {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab: TabKey = searchParams.get("tab") === "conflicts" ? "conflicts" : "batches";
  const conflictSource = searchParams.get("source") ?? undefined;
  const conflictBatchId = searchParams.get("batch_id") ?? undefined;
  const [resultBatchId, setResultBatchId] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["import-batches"],
    queryFn: () => listImportBatches({ page: 1, page_size: 50 }),
  });

  // 取不到 access 时一律按「不可切换」处理（新计数列显示「—」、不显示结果 / 冲突入口）
  const { data: access } = useQuery({
    queryKey: ["import-access"],
    queryFn: getImportAccess,
  });
  const configurable = new Set(
    (access ?? []).filter((a) => a.configurable).map((a) => a.source)
  );

  const retryMutation = useMutation({
    mutationFn: (id: string) => retryImportBatch(id),
    onSuccess: () => {
      message.success("已触发重试");
      void qc.invalidateQueries({ queryKey: ["import-batches"] });
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function setScope(next: { tab: TabKey; source?: string; batchId?: string }) {
    setSearchParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (next.tab === "conflicts") p.set("tab", "conflicts");
        else p.delete("tab");
        if (next.tab === "conflicts" && next.source) p.set("source", next.source);
        else p.delete("source");
        if (next.tab === "conflicts" && next.batchId) p.set("batch_id", next.batchId);
        else p.delete("batch_id");
        return p;
      },
      { replace: true }
    );
  }

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

  function countCell(r: ImportBatch, value: number) {
    return configurable.has(r.source) ? value : DASH;
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
    {
      title: (
        <Space size={4}>
          成功
          <Tooltip title={SUCCESS_HELP}>
            <QuestionCircleOutlined aria-label="成功列说明" style={{ color: "#8c8c8c" }} />
          </Tooltip>
        </Space>
      ),
      dataIndex: "imported",
      width: 90,
    },
    { title: "失败", dataIndex: "failed", width: 80 },
    {
      title: "仅补空",
      dataIndex: "filled",
      width: 80,
      render: (v: number, r) => countCell(r, v),
    },
    {
      title: "重复已跳过",
      dataIndex: "skipped",
      width: 100,
      render: (v: number, r) => countCell(r, v),
    },
    {
      title: "冲突",
      dataIndex: "conflicted",
      width: 130,
      render: (v: number, r) =>
        configurable.has(r.source) ? (
          <Space direction="vertical" size={0}>
            <span>{v} 行</span>
            {r.pending_conflicts > 0 && (
              <Typography.Text type="warning" style={{ fontSize: 12 }}>
                待处理 {r.pending_conflicts} 条
              </Typography.Text>
            )}
          </Space>
        ) : (
          DASH
        ),
    },
    {
      title: "补空",
      dataIndex: "filled_objects",
      width: 80,
      render: (v: number, r) => (configurable.has(r.source) ? `${v} 条` : DASH),
    },
    { title: "重试次数", dataIndex: "retry_count", width: 90 },
    {
      title: "创建时间",
      dataIndex: "created_at",
      width: 170,
      render: (v) => (v ? v.replace("T", " ").slice(0, 19) : "—"),
    },
    {
      title: "操作",
      width: 260,
      render: (_, r) => (
        <Space wrap size={0}>
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
          {configurable.has(r.source) && (
            <Button type="link" size="small" onClick={() => setResultBatchId(r.id)}>
              查看结果
            </Button>
          )}
          {configurable.has(r.source) && (r.conflicted > 0 || r.pending_conflicts > 0) && (
            <Button
              type="link"
              size="small"
              onClick={() => setScope({ tab: "conflicts", source: r.source, batchId: r.id })}
            >
              查看冲突
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const batchesTab = (
    <>
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
        scroll={{ x: 1600 }}
        pagination={false}
      />
    </>
  );

  return (
    <Card
      title={
        <Typography.Title level={4} style={{ margin: 0 }}>
          导入记录
        </Typography.Title>
      }
    >
      <Tabs
        activeKey={activeTab}
        onChange={(key) => setScope({ tab: key as TabKey })}
        items={[
          { key: "batches", label: "导入批次", children: batchesTab },
          {
            key: "conflicts",
            label: "冲突处理",
            children: (
              <ConflictPanel
                source={conflictSource}
                batchId={conflictBatchId}
                access={access}
                onScopeChange={(next) => setScope({ tab: "conflicts", ...next })}
              />
            ),
          },
        ]}
      />
      <ImportResultModal
        open={resultBatchId !== null}
        batchId={resultBatchId}
        onClose={() => setResultBatchId(null)}
      />
    </Card>
  );
}
