import { useEffect, useRef, useState } from "react";
import { Alert, Button, Modal, Space, Spin, Table, Tag, Tooltip, Typography } from "antd";
import { QuestionCircleOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { useNavigate } from "react-router-dom";
import { getImportBatch, getImportBatchNotes } from "@/features/import/api";
import { CONFLICT_FIELD_LABELS } from "@/features/import/conflictFields";
import { IMAGE_NOTE_STATUS, imageSummaryText } from "@/features/import/imageSummary";
import type { ImportJobNote, ImportJobStatus } from "@/features/import/types";
import { extractErrorMessage } from "@/services/apiClient";

const POLL_MS = 2000;
const POLL_LIMIT_MS = 120_000;
const NOTES_PAGE_SIZE = 10;

const ROW_STATUS: Record<ImportJobStatus, { label: string; color: string }> = {
  success: { label: "新增 / 已覆盖", color: "green" },
  filled: { label: "仅补空", color: "cyan" },
  skipped: { label: "重复已跳过", color: "default" },
  conflict: { label: "冲突", color: "orange" },
  failed: { label: "失败", color: "red" },
};

const COUNT_HELP =
  "「仅补空 b 行」是只补了空、其余都与系统相同的行数；「补空 N 条」是被补空的对象数（同一行里新增了 SKU 又补了商品简称的也算），每个对象留一条变更记录。";

const IMAGE_HELP =
  "按款计数：每款取文件里第一张内嵌图，只给还没有主图的款式补；已有主图的不覆盖。明细里「主图」列记在该款取图的那一行。";

export interface ImportResultModalProps {
  open: boolean;
  batchId: string | null;
  onClose: () => void;
}

/**
 * 一次导入的结果（8a-6，只对重复规则可切换的来源打开：商品资料、博主）。
 *
 * 每 2 秒轮询批次直到不再处理中（最多 2 分钟，超时提示去导入记录页看）；按行显示
 * 新增 / 仅补空 / 重复已跳过 / 冲突 / 失败，另显示补空对象数与内嵌主图款数（读过内嵌图时）；
 * 下面分页列出带提示、补空或主图结果的行。
 */
export function ImportResultModal({ open, batchId, onClose }: ImportResultModalProps) {
  const navigate = useNavigate();
  const startedAt = useRef<number>(Date.now());
  const [timedOut, setTimedOut] = useState(false);
  const [notesPage, setNotesPage] = useState(1);

  useEffect(() => {
    if (!open) return undefined;
    startedAt.current = Date.now();
    setTimedOut(false);
    setNotesPage(1);
    const timer = window.setTimeout(() => setTimedOut(true), POLL_LIMIT_MS);
    return () => window.clearTimeout(timer);
  }, [open, batchId]);

  const batchQuery = useQuery({
    queryKey: ["import-batches", "detail", batchId],
    queryFn: () => getImportBatch(batchId as string),
    enabled: open && !!batchId,
    // 处理中每 2 秒查一次，最多 2 分钟
    refetchInterval: (query) =>
      query.state.data?.status === "processing" &&
      Date.now() - startedAt.current < POLL_LIMIT_MS
        ? POLL_MS
        : false,
  });
  const batch = batchQuery.data;
  const done = !!batch && batch.status !== "processing";

  const notesQuery = useQuery({
    queryKey: ["import-batch-notes", batchId, notesPage],
    queryFn: () =>
      getImportBatchNotes(batchId as string, { page: notesPage, page_size: NOTES_PAGE_SIZE }),
    enabled: open && !!batchId && done,
  });

  const fieldLabels = batch ? CONFLICT_FIELD_LABELS[batch.source] ?? {} : {};

  const columns: ColumnsType<ImportJobNote> = [
    { title: "行号", dataIndex: "row_number", width: 70 },
    {
      title: "类别",
      dataIndex: "status",
      width: 120,
      render: (v: ImportJobStatus) => {
        const s = ROW_STATUS[v] ?? { label: v, color: "default" };
        return <Tag color={s.color}>{s.label}</Tag>;
      },
    },
    {
      title: "提示",
      dataIndex: "warnings",
      render: (ws: string[]) =>
        ws.length ? (
          <Space direction="vertical" size={0}>
            {ws.map((w, i) => (
              <Typography.Text key={i}>{w}</Typography.Text>
            ))}
          </Space>
        ) : (
          "—"
        ),
    },
    {
      title: "补空字段",
      dataIndex: "filled",
      width: 260,
      render: (items: ImportJobNote["filled"]) =>
        items.length ? (
          <Space direction="vertical" size={0}>
            {items.map((f, i) => (
              <Typography.Text key={i}>
                {f.object_label}：{f.fields.map((n) => fieldLabels[n] ?? n).join("、")}
              </Typography.Text>
            ))}
          </Space>
        ) : (
          "—"
        ),
    },
    {
      title: "主图",
      dataIndex: "image",
      width: 180,
      render: (img: ImportJobNote["image"]) => {
        if (!img) return "—";
        const s = IMAGE_NOTE_STATUS[img.status] ?? { label: img.status, color: "default" };
        return (
          <Space direction="vertical" size={0}>
            <Space size={4} wrap>
              <Tag color={s.color}>{s.label}</Tag>
              <Typography.Text>{img.style_code}</Typography.Text>
            </Space>
            {/* 原因与标签相同（如「未找到款式」）时不重复显示 */}
            {img.reason && img.reason !== s.label && (
              <Typography.Text type="secondary">{img.reason}</Typography.Text>
            )}
          </Space>
        );
      },
    },
  ];

  function goConflicts() {
    if (!batch) return;
    const params = new URLSearchParams({
      tab: "conflicts",
      source: batch.source,
      batch_id: batch.id,
    });
    onClose();
    navigate(`/imports?${params.toString()}`);
  }

  const hasConflicts = !!batch && (batch.conflicted > 0 || batch.pending_conflicts > 0);
  const imageText = batch ? imageSummaryText(batch.image_summary) : null;

  return (
    <Modal
      open={open}
      title="导入结果"
      onCancel={onClose}
      width={860}
      footer={
        <Space>
          {done && hasConflicts && (
            <Button type="primary" onClick={goConflicts}>
              去处理冲突
            </Button>
          )}
          <Button onClick={onClose}>关闭</Button>
        </Space>
      }
      destroyOnClose
    >
      {batchQuery.isError && (
        <Alert type="error" showIcon message={extractErrorMessage(batchQuery.error)} />
      )}
      {!batch && batchQuery.isLoading && <Spin />}
      {batch && !done && !timedOut && (
        <Space>
          <Spin size="small" />
          <Typography.Text>正在处理「{batch.original_filename}」，请稍候…</Typography.Text>
        </Space>
      )}
      {batch && !done && timedOut && (
        <Alert
          type="warning"
          showIcon
          message="处理时间较长"
          description="文件仍在后台处理，可以关闭本窗口，稍后到「导入记录」页查看结果。"
        />
      )}
      {done && batch && (
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Typography.Paragraph style={{ marginBottom: 0 }}>
            新增 {batch.imported} 行 · 仅补空 {batch.filled} 行 · 重复已跳过 {batch.skipped} 行 ·
            冲突 {batch.conflicted} 行 · 失败 {batch.failed} 行（共 {batch.total_rows} 行）；补空{" "}
            {batch.filled_objects} 条{" "}
            <Tooltip title={COUNT_HELP}>
              <QuestionCircleOutlined aria-label="计数说明" style={{ color: "#8c8c8c" }} />
            </Tooltip>
          </Typography.Paragraph>
          {imageText && (
            <Typography.Paragraph style={{ marginBottom: 0 }}>
              {imageText}{" "}
              <Tooltip title={IMAGE_HELP}>
                <QuestionCircleOutlined aria-label="主图计数说明" style={{ color: "#8c8c8c" }} />
              </Tooltip>
            </Typography.Paragraph>
          )}
          {batch.pending_conflicts > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`本批次还有 ${batch.pending_conflicts} 条冲突待处理（冲突的对象没有被改动）`}
            />
          )}
          {batch.failed > 0 && (
            <Alert
              type="error"
              showIcon
              message={`有 ${batch.failed} 行失败，可到「导入记录」页下载失败明细后改文件重导或重试`}
            />
          )}
          <Typography.Text strong>提示、补空与主图明细</Typography.Text>
          <Table
            rowKey="row_number"
            size="small"
            loading={notesQuery.isLoading}
            columns={columns}
            dataSource={notesQuery.data?.items ?? []}
            locale={{ emptyText: "没有提示、补空或主图结果" }}
            pagination={{
              current: notesPage,
              pageSize: NOTES_PAGE_SIZE,
              total: notesQuery.data?.total ?? 0,
              onChange: setNotesPage,
              showSizeChanger: false,
            }}
            scroll={{ x: 800 }}
          />
        </Space>
      )}
    </Modal>
  );
}
