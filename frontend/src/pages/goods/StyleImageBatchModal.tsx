import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Modal,
  Progress,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { CopyOutlined, InboxOutlined } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import { uploadStyleMainImagesBatch } from "@/features/product/api";
import {
  IMAGE_BATCH_EXTENSIONS,
  IMAGE_BATCH_SIZE,
  chunk,
  findDuplicateStems,
  hasAllowedExtension,
  imageStem,
  isRequestTimeout,
  missingResults,
} from "@/features/product/imageBatch";
import { compressStyleMainImage } from "@/features/product/imageCompression";
import type { StyleImageBatchStatus } from "@/features/product/types";
import { extractErrorMessage } from "@/services/apiClient";

/** 结果类别：后端的五种 + 前端的「结果未知」（超时，服务端可能已保存）。 */
type RowStatus = StyleImageBatchStatus | "unknown";

interface ResultRow {
  key: string;
  filename: string;
  styleCode: string | null;
  status: RowStatus;
  reason: string | null;
}

const STATUS_META: Record<RowStatus, { label: string; color: string }> = {
  created: { label: "新设", color: "green" },
  replaced: { label: "替换", color: "blue" },
  unmatched: { label: "未匹配", color: "default" },
  rejected: { label: "被拒", color: "orange" },
  failed: { label: "失败", color: "red" },
  unknown: { label: "结果未知", color: "gold" },
};
const STATUS_ORDER: RowStatus[] = ["created", "replaced", "unmatched", "rejected", "failed", "unknown"];

const MAX_BYTES = 300 * 1024;
const UNKNOWN_REASON = "结果未知（可能已保存），请刷新款式列表核对或重传";
const MISSING_REASON = "服务器未收到，请重传";

export interface StyleImageBatchModalProps {
  open: boolean;
  onClose: () => void;
}

function fileKey(f: File): string {
  return `${f.name}\u0000${f.size}\u0000${f.lastModified}`;
}

/**
 * 款式主图批量上传（8a-2，设计 §7.3）：文件名去扩展名 = 款号（不区分大小写）即匹配。
 *
 * 流程：整次选择先在本地预检（重名的全部、扩展名不支持的直接列「被拒」）→ 其余逐张压缩到
 * 300KB 以内（按「原款号.压缩后扩展名」重新命名）→ 每 10 张一个请求串行发送，边传边出结果，
 * 每批核对结果是否齐全 → 完成后刷新款式、商品、成本表。
 */
export function StyleImageBatchModal({ open, onClose }: StyleImageBatchModalProps) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<File[]>([]);
  const [rows, setRows] = useState<ResultRow[]>([]);
  const [running, setRunning] = useState(false);
  const [processed, setProcessed] = useState(0);
  const [total, setTotal] = useState(0);
  const [filter, setFilter] = useState<RowStatus | "all">("all");
  const cancelled = useRef(false);
  const rowSeq = useRef(0);

  useEffect(() => {
    if (!open) return;
    cancelled.current = false;
    setSelected([]);
    setRows([]);
    setRunning(false);
    setProcessed(0);
    setTotal(0);
    setFilter("all");
  }, [open]);

  const counts = useMemo(() => {
    const c: Record<RowStatus, number> = {
      created: 0,
      replaced: 0,
      unmatched: 0,
      rejected: 0,
      failed: 0,
      unknown: 0,
    };
    for (const r of rows) c[r.status] += 1;
    return c;
  }, [rows]);

  const visibleRows = filter === "all" ? rows : rows.filter((r) => r.status === filter);
  const unmatchedNames = rows.filter((r) => r.status === "unmatched").map((r) => r.filename);

  function addRows(next: ResultRow[]) {
    if (next.length) setRows((prev) => [...prev, ...next]);
  }

  function nextKey(): string {
    rowSeq.current += 1;
    return `r${rowSeq.current}`;
  }

  function localRow(filename: string, status: RowStatus, reason: string | null): ResultRow {
    return { key: nextKey(), filename, styleCode: null, status, reason };
  }

  async function sendBatch(files: File[], originalOf: Map<string, string>) {
    const sentNames = files.map((f) => f.name);
    const display = (sent: string) => originalOf.get(sent) ?? sent;
    try {
      const resp = await uploadStyleMainImagesBatch(files);
      const got: ResultRow[] = resp.results.map((r) => ({
        key: nextKey(),
        filename: display(r.filename),
        styleCode: r.style_code ?? null,
        status: r.status,
        reason: r.reason ?? null,
      }));
      // 对账：截断的请求体在服务端看不出来，缺了的不能悄悄漏掉
      const missing = missingResults(sentNames, resp.results).map((name) =>
        localRow(display(name), "failed", MISSING_REASON)
      );
      addRows([...got, ...missing]);
    } catch (err) {
      if (isRequestTimeout(err)) {
        addRows(sentNames.map((n) => localRow(display(n), "unknown", UNKNOWN_REASON)));
      } else {
        const msg = extractErrorMessage(err);
        addRows(sentNames.map((n) => localRow(display(n), "failed", msg)));
      }
    }
  }

  async function start() {
    if (!selected.length || running) return;
    cancelled.current = false;
    setRunning(true);
    setRows([]);
    setFilter("all");
    setProcessed(0);
    setTotal(selected.length);

    // 1）整次选择预检：重名按整次选择算（不是按每批 10 张）
    const duplicates = findDuplicateStems(selected);
    const rejected: ResultRow[] = [];
    const candidates: File[] = [];
    for (const f of selected) {
      if (duplicates.has(f.name)) rejected.push(localRow(f.name, "rejected", "同批重名"));
      else if (!hasAllowedExtension(f.name)) rejected.push(localRow(f.name, "rejected", "类型不支持"));
      else if (!imageStem(f.name)) rejected.push(localRow(f.name, "rejected", "文件名无效"));
      else candidates.push(f);
    }
    addRows(rejected);
    setProcessed(rejected.length);

    try {
      // 2）每 10 张：逐张压缩（控制内存）→ 一个请求串行发送
      for (const group of chunk(candidates, IMAGE_BATCH_SIZE)) {
        if (cancelled.current) break;
        const toSend: File[] = [];
        const originalOf = new Map<string, string>();
        const localRejected: ResultRow[] = [];
        for (const f of group) {
          try {
            const compressed = await compressStyleMainImage(f);
            if (compressed.compressedBytes >= MAX_BYTES) {
              localRejected.push(localRow(f.name, "rejected", "压缩后仍不小于 300KB"));
              continue;
            }
            const ext = compressed.format === "image/webp" ? "webp" : "jpg";
            // compressStyleMainImage 会改写文件名；按原款号重新命名，否则匹配不上
            const name = `${imageStem(f.name)}.${ext}`;
            toSend.push(new File([compressed.file], name, { type: compressed.format }));
            originalOf.set(name, f.name);
          } catch (error) {
            const reason = error instanceof Error ? error.message : String(error);
            localRejected.push(localRow(f.name, "rejected", `无法读取图片（${reason}）`));
          }
        }
        addRows(localRejected);
        if (toSend.length && !cancelled.current) await sendBatch(toSend, originalOf);
        setProcessed((p) => p + group.length);
      }
    } finally {
      setRunning(false);
      void qc.invalidateQueries({ queryKey: ["styles"] });
      void qc.invalidateQueries({ queryKey: ["goods"] });
      void qc.invalidateQueries({ queryKey: ["cost-table"] });
    }
  }

  function requestClose() {
    if (!running) {
      onClose();
      return;
    }
    Modal.confirm({
      title: "上传进行中，确定关闭？",
      content: "关闭后剩下的图片不再上传；已上传成功的保留。",
      okText: "关闭",
      cancelText: "继续上传",
      onOk: () => {
        cancelled.current = true;
        onClose();
      },
    });
  }

  async function copyUnmatched() {
    try {
      await navigator.clipboard.writeText(unmatchedNames.join("\n"));
      message.success(`已复制 ${unmatchedNames.length} 个文件名`);
    } catch {
      message.error("复制失败，请手动选择文本复制");
    }
  }

  const columns: ColumnsType<ResultRow> = [
    { title: "文件名", dataIndex: "filename", ellipsis: true },
    { title: "款号", dataIndex: "styleCode", width: 140, render: (v: string | null) => v || "—" },
    {
      title: "结果",
      dataIndex: "status",
      width: 100,
      render: (v: RowStatus) => <Tag color={STATUS_META[v].color}>{STATUS_META[v].label}</Tag>,
    },
    { title: "原因", dataIndex: "reason", width: 280, render: (v: string | null) => v || "—" },
  ];

  const percent = total ? Math.round((processed / total) * 100) : 0;

  return (
    <Modal
      title="批量上传主图"
      open={open}
      onCancel={requestClose}
      maskClosable={!running}
      width={880}
      destroyOnHidden
      footer={
        <Space>
          <Button onClick={requestClose}>{running ? "停止并关闭" : "关闭"}</Button>
          <Button
            type="primary"
            onClick={() => void start()}
            loading={running}
            disabled={!selected.length}
          >
            开始上传
          </Button>
        </Space>
      }
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
          文件名去掉扩展名后与款号一致（不区分大小写）即匹配，已有主图的直接替换。支持 JPG、PNG、WebP，
          上传前自动压缩到 300KB 以内；同一次选择里款号重复的文件全部不传。
        </Typography.Paragraph>
        <Upload.Dragger
          multiple
          accept={IMAGE_BATCH_EXTENSIONS.join(",")}
          showUploadList={false}
          disabled={running}
          beforeUpload={(file) => {
            setSelected((prev) =>
              prev.some((f) => fileKey(f) === fileKey(file)) ? prev : [...prev, file]
            );
            return false;
          }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined aria-hidden />
          </p>
          <p className="ant-upload-text">点击或拖入图片（可一次选几百张）</p>
        </Upload.Dragger>
        <Space wrap>
          <Typography.Text role="status">已选择 {selected.length} 个文件</Typography.Text>
          {selected.length > 0 && !running ? (
            <Button size="small" onClick={() => setSelected([])}>
              清空
            </Button>
          ) : null}
        </Space>

        {total > 0 ? (
          <>
            <div>
              <Typography.Text role="status">
                已处理 {processed} / {total}
              </Typography.Text>
              <Progress percent={percent} status={running ? "active" : "normal"} />
            </div>
            <Space wrap size={[8, 8]}>
              {STATUS_ORDER.map((s) => (
                <Tag key={s} color={STATUS_META[s].color}>
                  {STATUS_META[s].label} {counts[s]}
                </Tag>
              ))}
            </Space>
            {counts.unknown > 0 ? (
              <Alert
                type="warning"
                showIcon
                message="有批次等待超时：这些图可能已经保存，请刷新款式列表核对；重传只会再替换一次，可以放心重传。"
              />
            ) : null}
            <Space wrap>
              <Select
                aria-label="按结果筛选"
                value={filter}
                style={{ width: 140 }}
                onChange={(v: RowStatus | "all") => setFilter(v)}
                options={[
                  { label: "全部结果", value: "all" },
                  ...STATUS_ORDER.map((s) => ({ label: STATUS_META[s].label, value: s })),
                ]}
              />
              <Button
                icon={<CopyOutlined />}
                disabled={unmatchedNames.length === 0}
                onClick={() => void copyUnmatched()}
              >
                复制未匹配的文件名
              </Button>
            </Space>
            <Table
              rowKey="key"
              size="small"
              columns={columns}
              dataSource={visibleRows}
              scroll={{ x: 640 }}
              pagination={{ pageSize: 20, showSizeChanger: false, showTotal: (t) => `共 ${t} 条` }}
            />
          </>
        ) : null}
      </Space>
    </Modal>
  );
}
