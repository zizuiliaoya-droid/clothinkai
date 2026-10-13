// 店铺日手填（7e R4，设计 2.4）：全站推 / 直通车 / 引力魔方三项与仅退款的校验和请求体。
//
// 规则同后端 StoreDailyManualUpdate：空 = 清空（发 null）；金额 0 ~ 99,999,999.99、最多两位小数。
// 只发改了的字段：后端按 model_fields_set 写，没传的不动。未来日期由后端拦（422 STORE_DAILY_FUTURE_DATE）。
import type { StoreReportRow } from "../types";

export const MANUAL_FIELDS = [
  "ad_spend_total",
  "zhitongche_spend",
  "yinli_spend",
  "refund_only_amount",
] as const;

export type StoreManualField = (typeof MANUAL_FIELDS)[number];

/** 编辑中的值：InputNumber stringMode 的原文，null / 空串 = 清空。 */
export type StoreManualDraft = Record<StoreManualField, string | null>;

/** PUT /api/reports/store-daily/{day} 的请求体：只含改了的字段，null = 清空。 */
export type StoreManualPatch = Partial<Record<StoreManualField, string | null>>;

export const MANUAL_AMOUNT_ERROR = "金额需在 0 ~ 99,999,999.99 之间，最多两位小数";

const AMOUNT_PATTERN = /^\d+(\.\d{1,2})?$/;
const MAX_CENTS = 9_999_999_999;

function normalize(value: string | null | undefined): string | null {
  const text = (value ?? "").trim();
  return text === "" ? null : text;
}

/** 已通过 AMOUNT_PATTERN 的金额 → 分（整数比较，不走浮点）。 */
function toCents(text: string): number {
  const [int, frac = ""] = text.split(".");
  return Number(int) * 100 + Number(frac.padEnd(2, "0"));
}

/** 单个金额：空 → null（合法，表示清空）；负数、非数、科学计数、超上限、超两位小数 → 提示文案。 */
export function validateManualAmount(value: string | null | undefined): string | null {
  const text = normalize(value);
  if (text === null) return null;
  if (!AMOUNT_PATTERN.test(text) || toCents(text) > MAX_CENTS) return MANUAL_AMOUNT_ERROR;
  return null;
}

/** 有错的字段 → 提示文案；全对时为空对象。 */
export function manualErrors(draft: StoreManualDraft): Partial<Record<StoreManualField, string>> {
  const errors: Partial<Record<StoreManualField, string>> = {};
  for (const field of MANUAL_FIELDS) {
    const error = validateManualAmount(draft[field]);
    if (error) errors[field] = error;
  }
  return errors;
}

function sameAmount(a: string | null, b: string | null): boolean {
  if (a === null || b === null) return a === b;
  if (AMOUNT_PATTERN.test(a) && AMOUNT_PATTERN.test(b)) return toCents(a) === toCents(b);
  return a === b;
}

/** 只含改了的字段：清空发 null，新值发去掉首尾空白的原文；数值相同（40 与 40.00）不算改动。 */
export function manualPatch(original: StoreManualDraft, draft: StoreManualDraft): StoreManualPatch {
  const patch: StoreManualPatch = {};
  for (const field of MANUAL_FIELDS) {
    const before = normalize(original[field]);
    const after = normalize(draft[field]);
    if (!sameAmount(before, after)) patch[field] = after;
  }
  return patch;
}

/** 日行上的四个手填值，作为编辑的初值与改动比较的基准。 */
export function manualDraftFromRow(row: Pick<StoreReportRow, StoreManualField>): StoreManualDraft {
  return {
    ad_spend_total: row.ad_spend_total,
    zhitongche_spend: row.zhitongche_spend,
    yinli_spend: row.yinli_spend,
    refund_only_amount: row.refund_only_amount,
  };
}
