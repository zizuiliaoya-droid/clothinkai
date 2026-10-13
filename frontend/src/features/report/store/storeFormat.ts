// 店铺报表的显示格式（7e R4）。

/** 比率 → 百分比，两位小数；null（分母 0 / 负）显示「—」。 */
export const pct = (v: string | null) => (v == null ? "—" : `${(Number(v) * 100).toFixed(2)}%`);

/** 「已录 x / y 天」：x = 已录仅退款的天数，y = 有日行的天数。 */
export function recordedDaysLabel(recorded: number, total: number): string {
  return `已录 ${recorded} / ${total} 天`;
}
