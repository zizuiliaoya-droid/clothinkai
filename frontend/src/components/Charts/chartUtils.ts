/** 图表共用的小工具：空值判定、默认格式化、配色。 */

/** 只有有限数字才画；null / undefined / NaN / Infinity 都当空值。 */
export function isFiniteNumber(v: number | null | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export function defaultFormat(v: number): string {
  return v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

/** 超过 maxChars 的标签截断，完整文字放在 <title> 里。 */
export function truncateLabel(label: string, maxChars: number): string {
  return label.length > maxChars ? `${label.slice(0, maxChars - 1)}…` : label;
}

export const CHART_PALETTE = [
  "#4f46e5",
  "#0ea5e9",
  "#f97316",
  "#16a34a",
  "#e11d48",
  "#a855f7",
  "#ca8a04",
  "#0d9488",
  "#db2777",
  "#2563eb",
  "#64748b",
];

/** 轴标签用 slate-600，保证浅色背景下的对比度。 */
export const AXIS_TEXT = "#475569";
export const GRID_LINE = "#e2e8f0";
