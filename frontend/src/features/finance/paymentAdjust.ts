// 填付款金额：实付与应付的差（流程线 8.6、F3）。按分算，避免浮点误差。

function toCents(v: string | number): number | null {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? Math.round(n * 100) : null;
}

/**
 * 实付 − 应付（分）。应付看不到（null）或实付还没填时返回 null：前端不判，交给后端
 * （实付 ≠ 应付且没写说明 → 422 FLOW_GATE_MISSING）。
 */
export function paymentDiffCents(
  total: string | null | undefined,
  paid: string | number | null | undefined,
): number | null {
  if (total == null || paid == null || paid === "") return null;
  const t = toCents(total);
  const p = toCents(paid);
  return t == null || p == null ? null : p - t;
}

/** 弹窗里实时显示的一行：「与应付一致」/「与应付相差 ¥200.00」。 */
export function paymentDiffText(diffCents: number): string {
  if (diffCents === 0) return "与应付一致";
  return `与应付相差 ¥${(Math.abs(diffCents) / 100).toFixed(2)}`;
}
