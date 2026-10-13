import { useState } from "react";
import { Alert, Button, InputNumber, Modal, Skeleton, Table, Typography, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getPromotionPlatformSettings,
  updatePromotionPlatformSettings,
} from "@/features/settings/api";
import type {
  PromotionPlatformSetting,
  PromotionPlatformSettings,
} from "@/features/settings/api";
import { extractErrorMessage, isApiError } from "@/services/apiClient";

const QUERY_KEY = ["promotion-platform-settings"] as const;
const REFRESH_BUSY_CODE = "REPORT_SUMMARY_REFRESH_BUSY";
export const CONFIRM_TEXT = "保存后报表回到实时口径，约 1 小时内恢复";
export const BUSY_TEXT = "报表正在刷新，请稍后再试";
export const READONLY_TEXT = "只有 PR 主管和管理员可以修改";

type Field = "like_coefficient" | "hit_threshold";
/** 编辑中的一行：输入框清空时是 null（保存前拦下，不发请求）。 */
interface DraftRow {
  platform: string;
  like_coefficient: string | null;
  hit_threshold: number | null;
}
/** 单元格错误，键是 `行号:字段`（行号与 422 的 loc[1] 一致）。 */
type CellErrors = Record<string, string>;

const FIELD_ERROR_TEXT: Record<Field, string> = {
  like_coefficient: "系数须大于 0、不超过 10，最多 3 位小数",
  hit_threshold: "阈值须是 1 ~ 10,000,000 的整数",
};
// slate-600 / 深红：白底对比度都在 4.5:1 以上（antd 的 secondary / danger 不到）
const MUTED_STYLE = { color: "#475569" } as const;
const ERROR_STYLE = { color: "#b42318", fontSize: 12, marginTop: 2 } as const;

const cellKey = (index: number, field: Field) => `${index}:${field}`;

function sameRow(a: DraftRow, b: PromotionPlatformSetting): boolean {
  // 系数按数值比：服务端回 "0.100"，输入框里可能是 "0.1"
  return (
    a.like_coefficient !== null &&
    Number(a.like_coefficient) === Number(b.like_coefficient) &&
    a.hit_threshold === b.hit_threshold
  );
}

/** 422 的 details.errors → 单元格错误（loc = ["body", 行号, 字段]）与对不上单元格的整体错误。 */
function parseValidationErrors(err: unknown): { cells: CellErrors; others: string[] } | null {
  if (!isApiError(err)) return null;
  const errors = (
    err.response.data.details as { errors?: { loc?: unknown[]; msg?: string }[] } | undefined
  )?.errors;
  if (!Array.isArray(errors)) return null;
  const cells: CellErrors = {};
  const others: string[] = [];
  for (const e of errors) {
    const [, index, field] = e.loc ?? [];
    if (typeof index === "number" && (field === "like_coefficient" || field === "hit_threshold")) {
      cells[cellKey(index, field)] = FIELD_ERROR_TEXT[field];
    } else {
      others.push(e.msg ?? "格式不对");
    }
  }
  return { cells, others };
}

/**
 * 系统设置 ·「平台指标」（7e R7，报表视图设计 5.2）：5 个平台的点赞折算系数与爆文阈值。
 *
 * 读权限可见，`can_edit`（metric_config:write）才出输入框和保存按钮；只管显示，后端 403 才是闸门。
 * 保存前确认（会清汇总覆盖，报表约 1 小时内走实时）；撞刷新锁 409 提示稍后再试；422 按行号标到单元格。
 */
export function PromotionPlatformSettingTab() {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => getPromotionPlatformSettings(),
  });
  const [draft, setDraft] = useState<DraftRow[] | null>(null);
  const [cellErrors, setCellErrors] = useState<CellErrors>({});

  const saveMutation = useMutation({
    mutationFn: (items: PromotionPlatformSetting[]) => updatePromotionPlatformSettings(items),
    onSuccess: (res: PromotionPlatformSettings) => {
      qc.setQueryData(QUERY_KEY, res);
      setDraft(null);
      setCellErrors({});
      if (res.changed.length === 0) message.info("没有改动");
      else if (res.quality_recompute_queued) message.success("已保存，博主标签将在几分钟内更新");
      else message.success("已保存");
    },
    onError: (err) => {
      const resp = (err as { response?: { status?: number; data?: { code?: unknown } } }).response;
      if (resp?.status === 409 || resp?.data?.code === REFRESH_BUSY_CODE) {
        message.error(BUSY_TEXT);
        return;
      }
      const parsed = resp?.status === 422 ? parseValidationErrors(err) : null;
      if (parsed) {
        setCellErrors(parsed.cells);
        message.error(parsed.others[0] ?? "有数据不符合要求，请按提示修改");
        return;
      }
      message.error(extractErrorMessage(err));
    },
  });

  if (isLoading) return <Skeleton active />;
  if (error || !data) {
    return <Alert type="error" showIcon message={extractErrorMessage(error, "加载失败")} />;
  }

  const canEdit = data.can_edit;
  const saved = data.items;
  const rows: DraftRow[] = draft ?? saved;
  const dirty = draft !== null && draft.some((row, i) => !sameRow(row, saved[i]));

  function edit(index: number, field: Field, value: string | number | null) {
    setDraft((prev) =>
      (prev ?? saved).map((row, i) => (i === index ? { ...row, [field]: value } : row))
    );
    setCellErrors((prev) => {
      const next = { ...prev };
      delete next[cellKey(index, field)];
      return next;
    });
  }

  function save() {
    const missing: CellErrors = {};
    rows.forEach((row, i) => {
      if (row.like_coefficient === null) missing[cellKey(i, "like_coefficient")] = "必填";
      if (row.hit_threshold === null) missing[cellKey(i, "hit_threshold")] = "必填";
    });
    if (Object.keys(missing).length > 0) {
      setCellErrors(missing);
      return;
    }
    const payload = rows.map((row) => ({
      platform: row.platform,
      like_coefficient: row.like_coefficient as string,
      hit_threshold: row.hit_threshold as number,
    }));
    Modal.confirm({
      title: "保存平台指标？",
      content: CONFIRM_TEXT,
      okText: "确认保存",
      cancelText: "取消",
      // 只有点了确认才发请求；取消什么都不发
      onOk: () => saveMutation.mutate(payload),
    });
  }

  function errorOf(index: number, field: Field) {
    return cellErrors[cellKey(index, field)];
  }

  function renderInput(row: DraftRow, index: number, field: Field) {
    const err = errorOf(index, field);
    const errId = `platform-metric-${index}-${field}-error`;
    const common = {
      "aria-label": `${row.platform}${field === "like_coefficient" ? "点赞折算系数" : "爆文阈值"}`,
      "aria-invalid": err ? true : undefined,
      "aria-describedby": err ? errId : undefined,
      status: err ? ("error" as const) : undefined,
      style: { width: "100%", maxWidth: 160 },
    };
    return (
      <>
        {field === "like_coefficient" ? (
          <InputNumber<string>
            {...common}
            stringMode
            min="0.001"
            max="10"
            step="0.1"
            precision={3}
            value={row.like_coefficient}
            onChange={(v) => edit(index, field, v)}
          />
        ) : (
          <InputNumber<number>
            {...common}
            min={1}
            max={10_000_000}
            step={100}
            precision={0}
            value={row.hit_threshold}
            onChange={(v) => edit(index, field, v)}
          />
        )}
        {err && (
          <div id={errId} style={ERROR_STYLE}>
            {err}
          </div>
        )}
      </>
    );
  }

  const columns: ColumnsType<DraftRow> = [
    { title: "平台", dataIndex: "platform", width: 100 },
    {
      title: "点赞折算系数",
      dataIndex: "like_coefficient",
      width: 200,
      render: (_: unknown, row, index) =>
        canEdit ? renderInput(row, index, "like_coefficient") : `×${Number(row.like_coefficient)}`,
    },
    {
      title: "爆文阈值（点赞数 ≥）",
      dataIndex: "hit_threshold",
      width: 200,
      render: (_: unknown, row, index) =>
        canEdit
          ? renderInput(row, index, "hit_threshold")
          : (row.hit_threshold ?? 0).toLocaleString("zh-CN"),
    },
  ];

  return (
    <>
      <Typography.Paragraph style={MUTED_STYLE}>
        点赞数达到所在平台的爆文阈值算爆文（推广列表、工作进度爆文数、博主「带货型」都按它判）。
        折算点赞 = 点赞数 × 折算系数，用于报表的点赞数与 CPL。
      </Typography.Paragraph>
      {!canEdit && (
        <Alert type="info" showIcon style={{ marginBottom: 16 }} message={READONLY_TEXT} />
      )}
      <Table<DraftRow>
        rowKey="platform"
        size="middle"
        columns={columns}
        dataSource={rows}
        pagination={false}
        scroll={{ x: 500 }}
        style={{ maxWidth: 640 }}
      />
      {canEdit && (
        <Button
          type="primary"
          style={{ marginTop: 16 }}
          disabled={!dirty}
          loading={saveMutation.isPending}
          onClick={save}
        >
          保存修改
        </Button>
      )}
    </>
  );
}
