// U05 finance feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  AttachmentResponse,
  AttachmentUploadInitRequest,
  AttachmentUploadInitResponse,
  DailySummaryActivityResponse,
  DailySummaryAsOfResponse,
  Settlement,
  SettlementExtraItemCreateRequest,
  SettlementListFilters,
  SettlementPage,
  SettlementPaymentAmountRequest,
  SettlementPaymentProofRequest,
  SettlementReviewRequest,
} from "./types";

export async function listSettlements(
  filters: SettlementListFilters = {}
): Promise<SettlementPage> {
  const resp = await apiClient.get<SettlementPage>("/api/settlements/", {
    params: filters,
  });
  return resp.data;
}

export async function getSettlement(
  settlementId: string
): Promise<Settlement> {
  const resp = await apiClient.get<Settlement>(
    `/api/settlements/${settlementId}`
  );
  return resp.data;
}

// 状态推进

export async function reviewSettlement(
  settlementId: string,
  payload: SettlementReviewRequest
): Promise<Settlement> {
  const resp = await apiClient.put<Settlement>(
    `/api/settlements/${settlementId}/review`,
    payload
  );
  return resp.data;
}

export async function addExtraItem(
  settlementId: string,
  payload: SettlementExtraItemCreateRequest
): Promise<Settlement> {
  const resp = await apiClient.post<Settlement>(
    `/api/settlements/${settlementId}/extra-items`,
    payload
  );
  return resp.data;
}

export async function fillPaymentAmount(
  settlementId: string,
  payload: SettlementPaymentAmountRequest
): Promise<Settlement> {
  const resp = await apiClient.put<Settlement>(
    `/api/settlements/${settlementId}/payment-amount`,
    payload
  );
  return resp.data;
}

export async function uploadPaymentProof(
  settlementId: string,
  payload: SettlementPaymentProofRequest
): Promise<Settlement> {
  const resp = await apiClient.put<Settlement>(
    `/api/settlements/${settlementId}/payment-proof`,
    payload
  );
  return resp.data;
}

// 双口径汇总（FB7）

export async function getDailySummaryAsOf(
  date?: string
): Promise<DailySummaryAsOfResponse> {
  const resp = await apiClient.get<DailySummaryAsOfResponse>(
    "/api/settlements/daily-summary/as-of",
    { params: date ? { date } : {} }
  );
  return resp.data;
}

export async function getDailySummaryActivity(
  date?: string
): Promise<DailySummaryActivityResponse> {
  const resp = await apiClient.get<DailySummaryActivityResponse>(
    "/api/settlements/daily-summary/activity",
    { params: date ? { date } : {} }
  );
  return resp.data;
}

// shared attachment 基础设施（上传付款截图：upload-init → 直传 R2 → complete）

export async function initAttachmentUpload(
  payload: AttachmentUploadInitRequest
): Promise<AttachmentUploadInitResponse> {
  const resp = await apiClient.post<AttachmentUploadInitResponse>(
    "/api/attachments/upload-init",
    payload
  );
  return resp.data;
}

export async function completeAttachmentUpload(
  attachmentId: string
): Promise<AttachmentResponse> {
  const resp = await apiClient.post<AttachmentResponse>(
    `/api/attachments/${attachmentId}/complete`
  );
  return resp.data;
}

/**
 * 直传 R2：用 upload-init 返回的 presigned_url 直接 PUT 文件。
 * Content-Type 必须与 init 时声明的 mime_type 一致。
 * 注意：不走 apiClient（不带 Authorization header，直传 R2）。
 */
export async function putFileToR2(
  presignedUrl: string,
  file: File
): Promise<void> {
  const resp = await fetch(presignedUrl, {
    method: "PUT",
    headers: { "Content-Type": file.type },
    body: file,
  });
  if (!resp.ok) {
    throw new Error(`R2 直传失败: ${resp.status}`);
  }
}
