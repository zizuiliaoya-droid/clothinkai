// 款式主图批量上传的纯函数（8a-2，设计 §7.3）。弹窗 StyleImageBatchModal 用，vitest 覆盖。

/** 每个请求最多发几张（后端上限 20，按 10 张一批控制单个请求的体积与耗时）。 */
export const IMAGE_BATCH_SIZE = 10;

/** 允许选择的扩展名（与后端白名单 JPG / PNG / WebP 对应）。 */
export const IMAGE_BATCH_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"] as const;

/**
 * 文件名 → 用来匹配款号的 stem：去目录（`/` 与 `\`）、去最后一个扩展名、去首尾空白。
 * 与后端 `product/images.py::image_stem` 同一口径（两边用同一张用例表测）。
 */
export function imageStem(filename: string): string {
  const parts = filename.replace(/\\/g, "/").split("/");
  let name = parts[parts.length - 1] ?? "";
  const dot = name.lastIndexOf(".");
  if (dot > 0) name = name.slice(0, dot);
  return name.trim();
}

/** 扩展名是否在允许范围内（不区分大小写）。 */
export function hasAllowedExtension(filename: string): boolean {
  const lower = filename.toLowerCase();
  return IMAGE_BATCH_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/**
 * 按**整次选择**找出 stem 不区分大小写相同的文件名（重名的全部返回）。
 *
 * 不能按每批 10 张算：后端只能在一个请求里判重，`ABC.jpg`（第 1 批）和 `abc.png`（第 2 批）
 * 都会匹配款号 ABC，后传的那张会悄悄替换前一张。
 */
export function findDuplicateStems(files: ReadonlyArray<{ name: string }>): Set<string> {
  const byKey = new Map<string, string[]>();
  for (const f of files) {
    const key = imageStem(f.name).toLowerCase();
    if (!key) continue;
    const list = byKey.get(key) ?? [];
    list.push(f.name);
    byKey.set(key, list);
  }
  const out = new Set<string>();
  for (const names of byKey.values()) {
    if (names.length > 1) names.forEach((n) => out.add(n));
  }
  return out;
}

/** 按 size 切块（最后一块可能不足）。 */
export function chunk<T>(list: ReadonlyArray<T>, size: number): T[][] {
  if (size <= 0) throw new Error("chunk size 必须大于 0");
  const out: T[][] = [];
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size));
  return out;
}

/**
 * 对账：这批发出去的文件名里，结果中没有的（请求体被截断时服务端看不出来，只处理收到的文件）。
 * 同名按次数核对。
 */
export function missingResults(
  sentNames: ReadonlyArray<string>,
  results: ReadonlyArray<{ filename: string }>
): string[] {
  const remaining = new Map<string, number>();
  for (const r of results) remaining.set(r.filename, (remaining.get(r.filename) ?? 0) + 1);
  const missing: string[] = [];
  for (const name of sentNames) {
    const n = remaining.get(name) ?? 0;
    if (n > 0) remaining.set(name, n - 1);
    else missing.push(name);
  }
  return missing;
}

/**
 * 是否是请求超时（浏览器不等了，服务端可能仍在写入）。axios 1.7 超时的错误码是
 * ECONNABORTED（开了 transitional.clarifyTimeoutError 才是 ETIMEDOUT）；带 response 的不是超时。
 */
export function isRequestTimeout(err: unknown): boolean {
  if (typeof err !== "object" || err === null) return false;
  const e = err as { code?: unknown; response?: unknown };
  if (e.response) return false;
  return e.code === "ECONNABORTED" || e.code === "ETIMEDOUT";
}
