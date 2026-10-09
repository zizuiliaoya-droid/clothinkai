// 导入上传失败时的报错文案：文件超过大小上限时可追加来源相关的提示（8a 补充）。
import { extractErrorMessage, isApiError } from "@/services/apiClient";

const TOO_LARGE_CODE = "IMPORT_FILE_TOO_LARGE";
/** nginx 先挡下（413、无 JSON 体）时的基文案，与后端 ImportFileTooLargeError 一致 */
const TOO_LARGE_FALLBACK = "文件超过大小上限";

function httpStatus(err: unknown): number | undefined {
  if (!err || typeof err !== "object" || !("response" in err)) return undefined;
  const status = (err as { response?: { status?: unknown } }).response?.status;
  return typeof status === "number" ? status : undefined;
}

/**
 * 上传报错文案。后端 422 `IMPORT_FILE_TOO_LARGE` 或网关 413 视为文件超限：
 * 有 `tooLargeHint` 时在原文案后接「；提示」。其他错误照 extractErrorMessage。
 */
export function importUploadErrorMessage(err: unknown, tooLargeHint?: string): string {
  const apiTooLarge = isApiError(err) && err.response.data.code === TOO_LARGE_CODE;
  const gatewayTooLarge = !isApiError(err) && httpStatus(err) === 413;
  if (!apiTooLarge && !gatewayTooLarge) return extractErrorMessage(err);
  const base = apiTooLarge ? extractErrorMessage(err, TOO_LARGE_FALLBACK) : TOO_LARGE_FALLBACK;
  return tooLargeHint ? `${base}；${tooLargeHint}` : base;
}
