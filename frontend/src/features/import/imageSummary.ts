// 导入时读 WPS 内嵌图补款式主图的结果文案（8a 补充）。
import type { ImportImageNoteStatus, ImportImageSummary } from "./types";

/** 每行「主图」结果的标签与颜色（标签文字总显示，颜色不是唯一提示）。 */
export const IMAGE_NOTE_STATUS: Record<ImportImageNoteStatus, { label: string; color: string }> = {
  set: { label: "已补主图", color: "green" },
  kept: { label: "已有主图", color: "default" },
  invalid: { label: "图片无效", color: "orange" },
  skipped: { label: "未找到款式", color: "default" },
  failed: { label: "保存失败", color: "red" },
};

/**
 * 结果弹窗里的一行计数：「补了 / 已有主图跳过」总显示，其余为 0 时省略；
 * 批次没读过内嵌图（image_summary 为 null）时返回 null，不显示这一行。
 */
export function imageSummaryText(summary: ImportImageSummary | null | undefined): string | null {
  if (!summary) return null;
  const parts = [`补了 ${summary.set} 款`, `已有主图跳过 ${summary.kept} 款`];
  if (summary.invalid > 0) parts.push(`图片无效 ${summary.invalid} 款`);
  if (summary.skipped > 0) parts.push(`未找到款式 ${summary.skipped} 款`);
  if (summary.failed > 0) parts.push(`保存失败 ${summary.failed} 款`);
  return `内嵌主图：${parts.join(" · ")}`;
}
