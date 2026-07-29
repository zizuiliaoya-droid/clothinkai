// U04 promotion feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  Promotion,
  PromotionCancelRequest,
  PromotionCreate,
  PromotionListFilters,
  PromotionPage,
  PromotionPublishRequest,
  PromotionRecallStartRequest,
  PromotionReviewRequest,
  PromotionUpdate,
} from "./types";

export async function listPromotions(
  filters: PromotionListFilters = {}
): Promise<PromotionPage> {
  const resp = await apiClient.get<PromotionPage>("/api/promotions/", {
    params: filters,
  });
  return resp.data;
}

export async function getPromotion(
  promotionId: string
): Promise<Promotion> {
  const resp = await apiClient.get<Promotion>(
    `/api/promotions/${promotionId}`
  );
  return resp.data;
}

export async function createPromotion(
  payload: PromotionCreate
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>("/api/promotions/", payload);
  return resp.data;
}

export async function updatePromotion(
  promotionId: string,
  payload: PromotionUpdate
): Promise<Promotion> {
  const resp = await apiClient.patch<Promotion>(
    `/api/promotions/${promotionId}`,
    payload
  );
  return resp.data;
}

export interface PaymentQrUploadInit {
  attachment_id: string;
  presigned_url: string;
  expires_in_seconds: number;
}

export async function initPaymentQrUpload(
  promotionId: string,
  payload: { filename?: string; mime_type: string; size_bytes: number }
): Promise<PaymentQrUploadInit> {
  const resp = await apiClient.post<PaymentQrUploadInit>(
    `/api/promotions/${promotionId}/payment-qr/upload-init`, payload
  );
  return resp.data;
}

export async function bindPaymentQr(
  promotionId: string, attachmentId: string
): Promise<Promotion> {
  const resp = await apiClient.put<Promotion>(
    `/api/promotions/${promotionId}/payment-qr`,
    { payment_qr_attachment_id: attachmentId }
  );
  return resp.data;
}

export async function uploadPaymentQrFile(
  promotionId: string, file: File
): Promise<Promotion> {
  const formData = new FormData();
  formData.append("file", file, file.name);
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/payment-qr/upload`,
    formData
  );
  return resp.data;
}

export async function removePaymentQr(promotionId: string): Promise<void> {
  await apiClient.delete(`/api/promotions/${promotionId}/payment-qr`);
}

export async function updateWarehouseWaybill(
  promotionId: string, waybill: string
): Promise<Promotion> {
  const resp = await apiClient.patch<Promotion>(
    `/api/promotions/${promotionId}/warehouse-waybill`, { waybill }
  );
  return resp.data;
}

export async function deletePromotion(promotionId: string): Promise<void> {
  await apiClient.delete(`/api/promotions/${promotionId}`);
}

// 状态推进 6 个

export async function publishPromotion(
  promotionId: string,
  payload: PromotionPublishRequest
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/publish`,
    payload
  );
  return resp.data;
}

export async function cancelPromotion(
  promotionId: string,
  payload: PromotionCancelRequest
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/cancel`,
    payload
  );
  return resp.data;
}

export async function startRecallPromotion(
  promotionId: string,
  payload: PromotionRecallStartRequest = {}
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/recall/start`,
    payload
  );
  return resp.data;
}

export async function recallSuccessPromotion(
  promotionId: string,
  payload: { remark?: string | null } = {}
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/recall/success`,
    payload
  );
  return resp.data;
}

export async function recallFailurePromotion(
  promotionId: string,
  payload: { remark?: string | null } = {}
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/recall/failure`,
    payload
  );
  return resp.data;
}

export async function reviewPromotion(
  promotionId: string,
  payload: PromotionReviewRequest
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/review`,
    payload
  );
  return resp.data;
}
