/**
 * 「录入信息」保存时要发给后端的 source_extra 补丁（7a-5）。
 *
 * 后端 PATCH 按键合并：补丁里没出现的键不动，值为 null 就删这个键。所以这里只交
 * **相对打开弹窗时的快照改过的键**：
 * - 两边都按 `String(v ?? "").trim()` 比较，相同（含都为空、只差首尾空白）不提交
 * - 改成空（清空了这一项）→ null，后端删键
 * - 只看 fieldNames 里的表单字段；表单外的键（导入写的、已删掉的旧字段、
 *   仓库回填的发货单号）一个都不带，后端自然原样保留
 *
 * 以前是把整包 source_extra 发回去，弹窗开着期间仓库回填的发货单号会被旧快照冲掉。
 */
export function buildSourceExtraPatch(
  initial: Record<string, unknown>,
  values: Record<string, unknown>,
  fieldNames: readonly string[]
): Record<string, string | null> {
  const patch: Record<string, string | null> = {};
  for (const name of fieldNames) {
    const before = String(initial[name] ?? "").trim();
    const after = String(values[name] ?? "").trim();
    if (before === after) continue;
    patch[name] = after === "" ? null : after;
  }
  return patch;
}
