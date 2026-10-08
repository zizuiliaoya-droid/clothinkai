import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnsType } from "antd/es/table";
import {
  downloadImportConflicts,
  listImportConflicts,
  resolveImportConflicts,
} from "@/features/import/api";
import { conflictFieldOptions, conflictValueText } from "@/features/import/conflictFields";
import type {
  ConflictResolveRequest,
  ConflictResolveResponse,
  ConflictStatus,
  ConflictValue,
  ImportConflict,
  ImportSourceAccess,
} from "@/features/import/types";
import { extractErrorMessage } from "@/services/apiClient";

const PAGE_SIZE = 20;

const STATUS_META: Record<ConflictStatus, { label: string; color: string }> = {
  pending: { label: "待处理", color: "orange" },
  overwritten: { label: "已用文件覆盖", color: "blue" },
  kept: { label: "已保留系统值", color: "green" },
  superseded: { label: "被后续导入取代", color: "default" },
  invalid: { label: "失效（对象已删除）", color: "red" },
};

const OBJECT_TYPE_LABEL: Record<string, string> = {
  style: "款式",
  sku: "SKU",
  goods: "商品",
  blogger: "博主",
};

const OUTCOME_LABEL: Record<string, string> = {
  resolved: "已处理",
  stale: "系统值已变",
  gone: "对象已删除",
  not_pending: "已不是待处理",
  not_overwritable: "键冲突不能覆盖",
  invalid_value: "文件值不合法",
  error: "处理失败",
};

type StatusFilter = ConflictStatus | "all";

interface StaleItem {
  conflict: ImportConflict;
  current: Record<string, ConflictValue>;
  masked: string[];
}

export interface ConflictPanelProps {
  source?: string;
  batchId?: string;
  /** 来源 / 批次筛选变化时回调（外壳据此同步地址栏） */
  onScopeChange: (next: { source?: string; batchId?: string }) => void;
  /** GET /api/imports/access 的结果（只列可切换且可见的来源） */
  access: ImportSourceAccess[] | undefined;
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

function formatTime(v: string | null): string {
  return v ? v.replace("T", " ").slice(0, 19) : "—";
}

function expectedOf(c: ImportConflict): Record<string, ConflictValue> {
  return Object.fromEntries(c.fields.map((f) => [f.field, f.system]));
}

/**
 * 导入记录页「冲突处理」页签（8a-6，设计 §4.5、§14）。
 *
 * 按来源 / 批次 / 状态 / 字段筛选；勾选多条后「用文件覆盖」或「保留系统值」（二次确认列出将改的字段）。
 * 覆盖时每条都带上列表里看到的系统值，系统值已被人改过的那条返回当前值，确认后带新的期望值重发。
 * 受保护字段（成本价、采购价、报价、微信、手机）没有读权限时只显示「有差异」。
 */
export function ConflictPanel({ source, batchId, onScopeChange, access }: ConflictPanelProps) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<StatusFilter>("pending");
  const [field, setField] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(1);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [staleItems, setStaleItems] = useState<StaleItem[]>([]);
  const [downloading, setDownloading] = useState(false);

  const sourceOptions = useMemo(
    () =>
      (access ?? [])
        .filter((a) => a.configurable && a.can_view)
        .map((a) => ({ value: a.source, label: a.label })),
    [access]
  );
  const fieldOptions = conflictFieldOptions(source);

  const filters = {
    source,
    batch_id: batchId,
    status,
    field: source ? field : undefined,
  };

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["import-conflicts", filters, page],
    queryFn: () => listImportConflicts({ ...filters, page, page_size: PAGE_SIZE }),
  });
  const items = data?.items ?? [];
  const selected = items.filter((c) => selectedKeys.includes(c.id));

  function refresh() {
    setSelectedKeys([]);
    void qc.invalidateQueries({ queryKey: ["import-conflicts"] });
    void qc.invalidateQueries({ queryKey: ["import-batches"] });
  }

  function report(resp: ConflictResolveResponse, byId: Map<string, ImportConflict>) {
    const parts = Object.entries(resp.summary)
      .filter(([, n]) => n > 0)
      .map(([k, n]) => `${OUTCOME_LABEL[k] ?? k} ${n} 条`);
    const problems = resp.results.filter(
      (r) => r.outcome !== "resolved" && r.outcome !== "stale"
    );
    if (problems.length) {
      Modal.warning({
        title: `处理结果：${parts.join("，")}`,
        content: (
          <Space direction="vertical" size={2}>
            {problems.map((r) => (
              <Typography.Text key={r.id}>
                {byId.get(r.id)?.object_label ?? shortId(r.id)}：
                {OUTCOME_LABEL[r.outcome] ?? r.outcome}
                {r.message ? `（${r.message}）` : ""}
              </Typography.Text>
            ))}
          </Space>
        ),
      });
    } else {
      message.success(`处理结果：${parts.join("，")}`);
    }
    const stale: StaleItem[] = resp.results
      .filter((r) => r.outcome === "stale" && byId.has(r.id))
      .map((r) => ({
        conflict: byId.get(r.id) as ImportConflict,
        current: r.current_values ?? {},
        masked: r.masked_fields,
      }));
    setStaleItems(stale);
  }

  const resolveMutation = useMutation({
    mutationFn: (args: { payload: ConflictResolveRequest; conflicts: ImportConflict[] }) =>
      resolveImportConflicts(args.payload),
    onSuccess: (resp, args) => {
      report(resp, new Map(args.conflicts.map((c) => [c.id, c])));
      refresh();
    },
    onError: (err) => message.error(extractErrorMessage(err)),
  });

  function confirmOverwrite() {
    const targets = selected;
    Modal.confirm({
      title: `用文件覆盖 ${targets.length} 条冲突？`,
      width: 640,
      okText: "用文件覆盖",
      content: (
        <Space direction="vertical" size={4} style={{ maxHeight: 360, overflow: "auto" }}>
          <Typography.Text type="secondary">以下字段将改成文件里的值，并留下变更记录：</Typography.Text>
          {targets.map((c) => (
            <Typography.Text key={c.id}>
              {c.object_label}：{c.fields.map((f) => f.label).join("、")}
            </Typography.Text>
          ))}
        </Space>
      ),
      onOk: () =>
        resolveMutation.mutateAsync({
          payload: {
            decision: "overwrite",
            items: targets.map((c) => ({ id: c.id, expected_system_values: expectedOf(c) })),
          },
          conflicts: targets,
        }),
    });
  }

  function confirmKeep() {
    const targets = selected;
    Modal.confirm({
      title: `保留系统值，关闭 ${targets.length} 条冲突？`,
      okText: "保留系统值",
      content: "对象不做任何改动，冲突标为「已保留系统值」并记录处理人与时间。",
      onOk: () =>
        resolveMutation.mutateAsync({
          payload: { decision: "keep", items: targets.map((c) => ({ id: c.id })) },
          conflicts: targets,
        }),
    });
  }

  function resendStale() {
    const pending = staleItems;
    setStaleItems([]);
    resolveMutation.mutate({
      payload: {
        decision: "overwrite",
        items: pending.map((s) => ({ id: s.conflict.id, expected_system_values: s.current })),
      },
      conflicts: pending.map((s) => s.conflict),
    });
  }

  async function handleDownload() {
    setDownloading(true);
    try {
      const blob = await downloadImportConflicts(filters);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "import-conflicts.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      message.error(extractErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  }

  const columns: ColumnsType<ImportConflict> = [
    {
      title: "对象",
      dataIndex: "object_label",
      width: 200,
      render: (_, c) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{c.object_label}</Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {OBJECT_TYPE_LABEL[c.object_type] ?? c.object_type}
            {c.kind === "key" ? " · 键冲突" : ""}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "差异（系统值 → 文件值）",
      dataIndex: "fields",
      render: (_, c) => (
        <Space direction="vertical" size={2}>
          {c.kind === "key" && c.message && <Typography.Text type="warning">{c.message}</Typography.Text>}
          {c.fields.map((f) => (
            <Typography.Text key={f.field}>
              {f.label}：
              {f.masked
                ? "有差异"
                : `${conflictValueText(f.system_display, f.system)} → ${conflictValueText(
                    f.file_display,
                    f.file
                  )}`}
              {f.from_batch_id && (
                <Tag style={{ marginLeft: 6 }}>来自批次 {shortId(f.from_batch_id)}</Tag>
              )}
            </Typography.Text>
          ))}
        </Space>
      ),
    },
    {
      title: "批次 / 行号",
      width: 180,
      render: (_, c) => (
        <Space direction="vertical" size={0}>
          <Typography.Text ellipsis style={{ maxWidth: 170 }}>
            {c.batch_filename ?? (c.batch_id ? shortId(c.batch_id) : "—")}
          </Typography.Text>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            第 {c.row_numbers.join("、")} 行
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: "发现时间",
      dataIndex: "created_at",
      width: 160,
      render: (v: string) => formatTime(v),
    },
    {
      title: "状态",
      dataIndex: "status",
      width: 140,
      render: (v: ConflictStatus) => (
        <Tag color={STATUS_META[v]?.color}>{STATUS_META[v]?.label ?? v}</Tag>
      ),
    },
    {
      title: "处理人 / 时间",
      width: 170,
      render: (_, c) =>
        c.resolved_at ? (
          <Space direction="vertical" size={0}>
            <Typography.Text>{c.resolved_by_name ?? (c.resolved_by ? "—" : "系统")}</Typography.Text>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {formatTime(c.resolved_at)}
            </Typography.Text>
          </Space>
        ) : (
          "—"
        ),
    },
  ];

  const canOverwrite =
    selected.length > 0 && selected.every((c) => c.can_resolve && c.overwritable);
  const canKeep = selected.length > 0 && selected.every((c) => c.can_resolve);

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      <Space wrap>
        <Select
          aria-label="来源"
          placeholder="全部来源"
          allowClear
          style={{ width: 160 }}
          value={source}
          options={sourceOptions}
          onChange={(v?: string) => {
            setField(undefined);
            setPage(1);
            onScopeChange({ source: v, batchId });
          }}
        />
        {batchId && (
          <Tag closable onClose={() => onScopeChange({ source, batchId: undefined })}>
            批次 {shortId(batchId)}
          </Tag>
        )}
        <Select<StatusFilter>
          aria-label="状态"
          style={{ width: 170 }}
          value={status}
          onChange={(v) => {
            setStatus(v);
            setPage(1);
            setSelectedKeys([]);
          }}
          options={[
            ...Object.entries(STATUS_META).map(([value, m]) => ({
              value: value as StatusFilter,
              label: m.label,
            })),
            { value: "all", label: "全部状态" },
          ]}
        />
        <Select
          aria-label="字段"
          placeholder={source ? "按字段筛选" : "先选来源再按字段筛选"}
          allowClear
          disabled={!source || fieldOptions.length === 0}
          style={{ width: 170 }}
          value={source ? field : undefined}
          options={fieldOptions}
          onChange={(v?: string) => {
            setField(v);
            setPage(1);
          }}
        />
        <Button loading={downloading} onClick={handleDownload}>
          下载 CSV
        </Button>
      </Space>

      <Space wrap>
        <Typography.Text type="secondary">已选 {selected.length} 条</Typography.Text>
        <Button
          type="primary"
          disabled={!canOverwrite}
          loading={resolveMutation.isPending}
          onClick={confirmOverwrite}
        >
          用文件覆盖
        </Button>
        <Button disabled={!canKeep} loading={resolveMutation.isPending} onClick={confirmKeep}>
          保留系统值
        </Button>
        {selected.some((c) => c.kind === "key") && (
          <Typography.Text type="secondary">
            键冲突只能「保留系统值」，要改归属请到成本表 / 商品页处理
          </Typography.Text>
        )}
      </Space>

      {isError && <Alert type="error" showIcon message={extractErrorMessage(error)} />}

      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={items}
        scroll={{ x: 1100 }}
        rowSelection={{
          selectedRowKeys: selectedKeys,
          onChange: (keys) => setSelectedKeys(keys.map(String)),
          getCheckboxProps: (c) => ({
            disabled: c.status !== "pending" || !c.can_resolve,
            "aria-label": `选择 ${c.object_label}`,
          }),
        }}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          total: data?.total ?? 0,
          showSizeChanger: false,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p) => {
            setPage(p);
            setSelectedKeys([]);
          },
        }}
      />

      <Modal
        open={staleItems.length > 0}
        title="系统值已被修改，请确认当前值"
        okText="确认后用文件覆盖"
        onOk={resendStale}
        onCancel={() => setStaleItems([])}
        width={640}
      >
        <Space direction="vertical" size={6} style={{ width: "100%" }}>
          <Typography.Text type="secondary">
            以下冲突生成之后，系统里的值已被人改过，本次没有覆盖。确认后会以下面的「当前值」为准重新提交。
          </Typography.Text>
          {staleItems.map((s) => (
            <div key={s.conflict.id}>
              <Typography.Text strong>{s.conflict.object_label}</Typography.Text>
              {s.conflict.fields.map((f) => (
                <div key={f.field}>
                  <Typography.Text>
                    {f.label}：当前{" "}
                    {s.masked.includes(f.field)
                      ? "有差异"
                      : conflictValueText(null, s.current[f.field] ?? null)}{" "}
                    → 文件 {f.masked ? "有差异" : conflictValueText(f.file_display, f.file)}
                  </Typography.Text>
                </div>
              ))}
            </div>
          ))}
        </Space>
      </Modal>
    </Space>
  );
}
