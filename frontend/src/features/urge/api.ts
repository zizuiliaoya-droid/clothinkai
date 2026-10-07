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
