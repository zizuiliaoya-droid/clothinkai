// 催发任务 API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  UrgeBatchResult,
  UrgeConfig,
  UrgeDashboard,
  UrgeRecord,
  UrgeTaskDetail,
  UrgeTaskFilters,
  UrgeTaskPage,
} from "./types";

export async function getUrgeConfig(): Promise<UrgeConfig> {
  const resp = await apiClient.get<UrgeConfig>("/api/urge/config");
  return resp.data;
}

export async function updateUrgeConfig(payload: UrgeConfig): Promise<UrgeConfig> {
  const resp = await apiClient.put<UrgeConfig>("/api/urge/config", payload);
  return resp.data;
}

export async function getUrgeDashboard(): Promise<UrgeDashboard> {
  const resp = await apiClient.get<UrgeDashboard>("/api/urge/dashboard");
  return resp.data;
}

export async function listUrgeTasks(
  filters: UrgeTaskFilters = {}
): Promise<UrgeTaskPage> {
  const resp = await apiClient.get<UrgeTaskPage>("/api/urge/tasks", {
    params: filters,
  });
  return resp.data;
}

export async function getUrgeTask(taskId: string): Promise<UrgeTaskDetail> {
  const resp = await apiClient.get<UrgeTaskDetail>(`/api/urge/tasks/${taskId}`);
  return resp.data;
}

export async function closeUrgeTask(
  taskId: string,
  reason?: string
): Promise<UrgeTaskDetail> {
  const resp = await apiClient.post<UrgeTaskDetail>(
    `/api/urge/tasks/${taskId}/close`,
    { reason: reason ?? null }
  );
  return resp.data;
}

/** 手动催发单条。任务不存在后端会现建。 */
export async function urgePromotion(
  promotionId: string,
  note?: string
): Promise<UrgeTaskDetail> {
  const resp = await apiClient.post<UrgeTaskDetail>(
    `/api/urge/promotions/${promotionId}/urge`,
    { note: note ?? null }
  );
  return resp.data;
}

/** 催发截图允许的格式与大小（与后端 check_image_payload、urge _SCREENSHOT_MAX_BYTES 一致）。 */
export const URGE_SCREENSHOT_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
export const URGE_SCREENSHOT_MAX_BYTES = 10 * 1024 * 1024;

const ALLOWED_TYPES: readonly string[] = URGE_SCREENSHOT_MIME_TYPES;

/**
 * 和后端 check_image_payload 同样的顺序与措辞（后端还会核对文件头，这里只挡格式与大小）。
 * 催发弹窗与标记完成弹窗共用（后端两处都用 label「催发截图」）。
 */
export function screenshotProblem(file: File): string | null {
  if (!ALLOWED_TYPES.includes(file.type)) return "催发截图仅支持 JPG、PNG、WebP 格式";
  if (file.size === 0) return "催发截图文件不能为空";
  if (file.size > URGE_SCREENSHOT_MAX_BYTES) return "催发截图文件不能超过 10MB";
  return null;
}

/**
 * 手动催发并附聊天截图。
 *
 * 走 multipart 让后端代传 R2，不用前端直传 —— 与收款码、款式主图一致，
 * 省掉 bucket CORS 配置。
 */
export async function urgePromotionWithScreenshot(
  promotionId: string,
  file: File,
  note?: string
): Promise<UrgeTaskDetail> {
  const form = new FormData();
  form.append("screenshot", file);
  if (note) form.append("note", note);
  const resp = await apiClient.post<UrgeTaskDetail>(
    `/api/urge/promotions/${promotionId}/urge-with-screenshot`,
    form
  );
  return resp.data;
}

/**
 * 标记完成（流程线 3.9 U1）：进行中 → 已关闭 / 已完成，截图必传。
 *
 * 同样走 multipart 由后端代传；字段名是 file（与 urge-with-screenshot 的 screenshot 不同）。
 */
export async function completeUrgeTask(
  taskId: string,
  file: File,
  note?: string
): Promise<UrgeTaskDetail> {
  const form = new FormData();
  form.append("file", file);
  if (note) form.append("note", note);
  const resp = await apiClient.post<UrgeTaskDetail>(
    `/api/urge/tasks/${taskId}/complete`,
    form
  );
  return resp.data;
}

/** react-query 键：推广列表抽屉的「催发记录」页签；催发成功后 invalidate。 */
export function promotionUrgeRecordsKey(promotionId: string) {
  return ["promotion-urge-records", promotionId] as const;
}

/** 按推广单取催发时间线。没有任务返回空数组。 */
export async function listPromotionUrgeRecords(
  promotionId: string
): Promise<UrgeRecord[]> {
  const resp = await apiClient.get<UrgeRecord[]>(
    `/api/urge/promotions/${promotionId}/records`
  );
  return resp.data;
}

/** 按款式批量催发。不支持截图。 */
export async function urgeBatch(
  styleId: string,
  note?: string
): Promise<UrgeBatchResult> {
  const resp = await apiClient.post<UrgeBatchResult>("/api/urge/batch", {
    style_id: styleId,
    note: note ?? null,
  });
  return resp.data;
}
