import dayjs from "dayjs";
import type { GoodsMember } from "@/features/promotion/types";
import type { Negotiation, NegotiationCreate } from "./types";
import type { NegotiationFormValues } from "./negotiationForm";
import { BULK_ACTION_UI, type BulkAction, type BulkResult } from "./bulkApi";

export interface CreateDraftRow {
  key: string;
  title: string;
  bloggerLabel: string;
  payload: NegotiationCreate;
  members: GoodsMember[];
  result?: BulkResult;
  /** 结果尚未核实时固定原请求，不能换内容重试。 */
  uncertain?: boolean;
  frozen?: NegotiationCreate;
  preparationError?: string;
}
export function draftValues(payload: NegotiationCreate): NegotiationFormValues {
  return {...payload, scheduled_publish_date: payload.scheduled_publish_date ? dayjs(payload.scheduled_publish_date) : null,
    quote_amount: payload.quote_amount == null ? null : Number(payload.quote_amount),
    items: Object.fromEntries((payload.items ?? []).filter(i => i.sku_id).map(i => [i.style_id, i.sku_id!]))};
}
export function actionBlock(row: Negotiation, action: BulkAction): string | null {
  const rule = row.ui?.actions?.[BULK_ACTION_UI[action]];
  if (!rule) return "当前没有此操作权限";
  if (rule.state !== "enabled") return rule.reason || rule.hint || "当前阶段不可执行";
  if (action === "submit" && rule.missing?.length) return `请先编辑补齐：${rule.missing.map(m => m.label).join("、")}`;
  return null;
}
export function localResult(key: string, message: string, status: BulkResult["status"] = "skipped", code = "NOT_EXECUTED"): BulkResult {
  return {row_key: key, negotiation_id: null, status, code, message, reused: false, negotiation: null};
}
export function resultLabel(result?: BulkResult, uncertain = false): string {
  if (uncertain) return "待核对：处理结果尚未确认，请先核对再重试";
  if (!result) return "未执行";
  return `${result.status === "success" ? result.reused ? "成功（已存在，未重复创建）" : "成功" : result.status === "skipped" ? "未执行" : "失败"}：${result.message}`;
}
/** 内部错误也可能发生在提交之后，不能当作已回滚的业务失败。 */
export function needsResultCheck(result?: BulkResult): boolean {
  return !result || result.code === "INTERNAL_ERROR";
}
/** 不按整包 HTTP 状态推断逐行成功；缺失或不确定结果必须保留原请求。 */
export function mergeCreateResults(rows: CreateDraftRow[], sentKeys: string[], results: BulkResult[]): CreateDraftRow[] {
  return rows.map(row => {
    if (!sentKeys.includes(row.key)) return row;
    const result = results.find(r => r.row_key === row.key);
    const uncertain = needsResultCheck(result);
    return {...row, result, uncertain, frozen: uncertain ? row.frozen : undefined};
  });
}
