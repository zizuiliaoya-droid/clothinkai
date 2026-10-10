// 没有明细的旧单：按「录入信息」里的颜色及规格原文给推送弹窗预选 SKU（流程线 8.4 ShipPushModal）。
//
// 旧写法是 `${color}${size ? " " + size : ""}`（以前录入信息的颜色及规格下拉），所以按同样的拼法比：
// 两边都去首尾空白、连续空白（含全角空格）压成一个；恰好一个 SKU 对上才预选，对不上或同色同码有多个都返回 null。

export interface LegacySkuOption {
  id: string;
  color: string;
  size: string;
}

function squash(v: string | null | undefined): string {
  return (v ?? "").replace(/[\s\u3000]+/g, " ").trim();
}

function specOf(sku: LegacySkuOption): string {
  return squash(`${squash(sku.color)} ${squash(sku.size)}`);
}

export function matchLegacyColorSpec(
  spec: string | null | undefined,
  skus: readonly LegacySkuOption[]
): string | null {
  const want = squash(spec);
  if (!want) return null;
  const hits = skus.filter((s) => specOf(s) === want);
  return hits.length === 1 ? hits[0].id : null;
}
