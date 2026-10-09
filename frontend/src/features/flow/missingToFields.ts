// 422 FLOW_GATE_MISSING 的 missing → 表单字段（流程线设计 8.2）。
// 能映射的交给 `form.setFields` 标红「必填」；映射不到的拼成一行「缺：…」给弹窗顶部显示。

import type { GateMissingItem } from "./keys";

export type FormFieldName = string | number | (string | number)[];

export interface MissingFieldError {
  name: FormFieldName;
  errors: string[];
}

export interface MissingToFieldsResult {
  fields: MissingFieldError[];
  /** 映射不到的缺项，形如「缺：收款码、品牌词评论截图」；没有就是 null。 */
  rest: string | null;
}

export function missingToFields(
  missing: readonly GateMissingItem[],
  fieldMap: Partial<Record<string, FormFieldName>>,
): MissingToFieldsResult {
  const fields: MissingFieldError[] = [];
  const unmapped: string[] = [];
  for (const item of missing) {
    const name = fieldMap[item.key];
    if (name === undefined) unmapped.push(item.label);
    else fields.push({ name, errors: ["必填"] });
  }
  return { fields, rest: unmapped.length ? `缺：${unmapped.join("、")}` : null };
}

function isGateMissingItem(v: unknown): v is GateMissingItem {
  if (!v || typeof v !== "object") return false;
  const o = v as Record<string, unknown>;
  return typeof o.key === "string" && typeof o.label === "string";
}

/** 从 axios 错误里取 FLOW_GATE_MISSING 的 missing；别的错误、形状不对都返回 null。 */
export function extractGateMissing(err: unknown): GateMissingItem[] | null {
  if (!err || typeof err !== "object") return null;
  const data = (err as { response?: { data?: { code?: unknown; details?: unknown } } }).response?.data;
  if (!data || data.code !== "FLOW_GATE_MISSING") return null;
  const missing = (data.details as { missing?: unknown } | undefined)?.missing;
  if (!Array.isArray(missing) || !missing.every(isGateMissingItem)) return null;
  return missing;
}
