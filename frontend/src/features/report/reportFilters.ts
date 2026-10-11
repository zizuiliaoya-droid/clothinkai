// 报表页筛选的初始值：URL 参数 → 筛选记忆 → 页面默认（7e R1，设计 2.1）。纯函数，便于测试；hook 在 useReportFilters.ts。
import type { TimePreset } from "./types";

export const TIME_PRESETS: readonly TimePreset[] = [
  "last_7d",
  "last_30d",
  "this_month",
  "last_month",
  "custom",
];

/**
 * 能从 URL 读的键（设计 2.1 白名单）。schema 里 `url: true` 但不在这里的键（如 granularity）一律不从 URL 读。
 * 多值键（season）在 URL 里写成重复键 `season=a&season=b`，与 axios 发给后端的格式一致。
 */
export const URL_FILTER_KEYS: readonly string[] = [
  "preset",
  "date_from",
  "date_to",
  "season",
  "pr_id",
  "goods_id",
  "channel",
  "tab",
  "exclude_brushing",
];

/** 季节多选上限与单个长度上限，与下钻接口的后端校验一致。 */
const MAX_LIST_ITEMS = 20;
const MAX_LIST_ITEM_LENGTH = 64;

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** 「未分配」PR 的取值（后端 pr_id=unassigned）。 */
export const UNASSIGNED_PR = "unassigned";

export type FilterFieldSpec = {
  /** 能否从 URL 读（还要在 URL_FILTER_KEYS 里）。 */
  url: boolean;
  /** 是否写进筛选记忆。 */
  memory: boolean;
} & (
  | { kind: "enum"; values: readonly string[] }
  | { kind: "date" }
  | { kind: "pr_id" }
  | { kind: "uuid" }
  | { kind: "string_list" }
  | { kind: "boolean" }
);

/** 每页的筛选白名单：键 = 页面筛选状态的字段。 */
export type ReportFilterSchema<T> = { [K in keyof T]-?: FilterFieldSpec };

function isRealDate(s: string): boolean {
  if (!DATE_RE.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

function validList(items: unknown[]): items is string[] {
  return (
    items.length <= MAX_LIST_ITEMS &&
    items.every(
      (s) => typeof s === "string" && s.trim() !== "" && s.length <= MAX_LIST_ITEM_LENGTH
    )
  );
}

/** 单个标量值（URL 字符串或记忆里的 JSON 值）按类型校验；不合格返回 undefined。 */
function parseScalar(spec: FilterFieldSpec, value: unknown, fromUrl: boolean): unknown {
  switch (spec.kind) {
    case "enum":
      return typeof value === "string" && spec.values.includes(value) ? value : undefined;
    case "date":
      return typeof value === "string" && isRealDate(value) ? value : undefined;
    case "pr_id":
      return typeof value === "string" && (value === UNASSIGNED_PR || UUID_RE.test(value))
        ? value
        : undefined;
    case "uuid":
      return typeof value === "string" && UUID_RE.test(value) ? value : undefined;
    case "boolean":
      if (typeof value === "boolean") return value;
      if (fromUrl && value === "true") return true;
      if (fromUrl && value === "false") return false;
      return undefined;
    default:
      return undefined;
  }
}

/**
 * 时间三件套（preset / date_from / date_to）成组校验：日期不成对或起 > 止 → 两个日期都丢；
 * URL 只带起止日期 → 推定 custom；URL 的 custom 没日期 → 丢 preset（坏链接回落记忆 / 默认）；
 * 预设不是 custom 时日期没用，丢掉。
 */
function normalizeTimeRange(out: Record<string, unknown>, fromUrl: boolean): void {
  const from = out.date_from as string | undefined;
  const to = out.date_to as string | undefined;
  if (from === undefined || to === undefined || from > to) {
    delete out.date_from;
    delete out.date_to;
  }
  const hasRange = out.date_from !== undefined;
  if (!fromUrl) return;
  if (hasRange && out.preset === undefined) out.preset = "custom";
  if (out.preset === "custom" && !hasRange) delete out.preset;
  if (out.preset !== undefined && out.preset !== "custom") {
    delete out.date_from;
    delete out.date_to;
  }
}

function hasTimeFields<T>(schema: ReportFilterSchema<T>): boolean {
  return "preset" in schema && "date_from" in schema && "date_to" in schema;
}

/** schema 里能从 URL 读的键。 */
export function schemaUrlKeys<T>(schema: ReportFilterSchema<T>): string[] {
  return (Object.keys(schema) as Array<keyof T & string>).filter(
    (k) => schema[k].url && URL_FILTER_KEYS.includes(k)
  );
}

/** URL → 筛选值：不认识或格式不对的键单个丢弃，其余照用。 */
export function parseUrlFilters<T>(
  params: URLSearchParams,
  schema: ReportFilterSchema<T>
): Partial<T> {
  const out: Record<string, unknown> = {};
  for (const key of schemaUrlKeys(schema)) {
    const spec = schema[key as keyof T];
    if (spec.kind === "string_list") {
      const items = params.getAll(key);
      if (items.length && validList(items)) out[key] = items;
      continue;
    }
    const raw = params.get(key);
    if (raw === null) continue;
    const value = parseScalar(spec, raw, true);
    if (value !== undefined) out[key] = value;
  }
  if (hasTimeFields(schema)) normalizeTimeRange(out, true);
  return out as Partial<T>;
}

/** 存量记忆 → 筛选值：只认 memory 标记的键，类型不对单个丢弃；非对象当作没存过。 */
export function restoreMemoryFilters<T>(
  saved: unknown,
  schema: ReportFilterSchema<T>
): Partial<T> {
  if (saved === null || typeof saved !== "object" || Array.isArray(saved)) return {};
  const raw = saved as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(schema) as Array<keyof T & string>) {
    const spec = schema[key];
    if (!spec.memory || !(key in raw)) continue;
    const value = raw[key];
    if (spec.kind === "string_list") {
      if (Array.isArray(value) && validList(value)) out[key] = value;
      continue;
    }
    const parsed = parseScalar(spec, value, false);
    if (parsed !== undefined) out[key] = parsed;
  }
  if (hasTimeFields(schema)) normalizeTimeRange(out, false);
  return out as Partial<T>;
}

/** 初始化顺序：URL → 记忆 → 页面默认，逐键覆盖。 */
export function initialFilters<T>(url: Partial<T>, memory: Partial<T>, defaults: T): T {
  return { ...defaults, ...memory, ...url };
}

/** 页面状态 → 要保存的记忆：只写 memory 标记的键（没值的键不写）。 */
export function pickMemoryFilters<T>(state: T, schema: ReportFilterSchema<T>): Partial<T> {
  const out: Partial<T> = {};
  for (const key of Object.keys(schema) as Array<keyof T>) {
    if (schema[key].memory && state[key] !== undefined) out[key] = state[key];
  }
  return out;
}
