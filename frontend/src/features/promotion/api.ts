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
  PromotionAmountLog,
  PromotionReviewRequest,
  PromotionUpdate,
  Retrospective,
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

/**
 * 上传博主寄回衣服单号。寄拍模式审核通过的前提 —— 没有单号后端会拒绝过审。
 * 与仓库发货单号是两个方向：那个寄给博主，这个博主寄回来。
 */
export async function setReturnWaybill(
  promotionId: string,
  returnWaybill: string
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/return-waybill`,
    { return_waybill: returnWaybill }
  );
  return resp.data;
}

// ---------------------------------------------------------------------------
// 复盘（PRD V1.4 改动 4）
// ---------------------------------------------------------------------------

/**
 * 录发布满 7 天的数据，推进到「待复盘」。
 *
 * 走 multipart 是因为截图必传 —— 三个指标和图得在同一个请求里，分两步会出现
 * 「数字录了图没传」的中间态。要求单据已结款。
 */
export async function recordMetrics(
  promotionId: string,
  metrics: {
    like_count: number;
    collect_count: number;
    comment_count: number;
  },
  screenshot: File
): Promise<Promotion> {
  const form = new FormData();
  form.append("like_count", String(metrics.like_count));
  form.append("collect_count", String(metrics.collect_count));
  form.append("comment_count", String(metrics.comment_count));
  form.append("screenshot", screenshot);
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/metrics`,
    form
  );
  return resp.data;
}

/** PR 提交复盘文字，推进到「待确认」。被打回后可以再提交，旧版留在档案里。 */
export async function submitRetrospective(
  promotionId: string,
  content: string
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/retrospective`,
    { content }
  );
  return resp.data;
}

/** 主管确认复盘（→ 已完成）或打回（→ 待复盘，必须写意见）。不能确认自己写的。 */
export async function confirmRetrospective(
  promotionId: string,
  approve: boolean,
  opinion?: string
): Promise<Promotion> {
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/retrospective/confirm`,
    { approve, opinion: opinion ?? null }
  );
  return resp.data;
}

/** 某博主的历史复盘，倒序。只返回主管确认过的。 */
export async function bloggerRetrospectives(
  bloggerId: string,
  limit = 20
): Promise<Retrospective[]> {
  const resp = await apiClient.get<Retrospective[]>(
    `/api/bloggers/${bloggerId}/retrospectives`,
    { params: { limit } }
  );
  return resp.data;
}

// ---------------------------------------------------------------------------
// 品牌词评论截图 + 金额时间线（PRD 改动 5 / 第 10 节第 14 条）
// ---------------------------------------------------------------------------

/**
 * 上传品牌词评论截图。不限状态，发布前后都能传。
 *
 * 没有这张图 publish 会 422 —— 后端的硬门槛，不是前端提示。
 */
export async function uploadBrandComment(
  promotionId: string,
  file: File
): Promise<Promotion> {
  const form = new FormData();
  form.append("file", file);
  const resp = await apiClient.post<Promotion>(
    `/api/promotions/${promotionId}/brand-comment`,
    form
  );
  return resp.data;
}

/**
 * 金额变更时间线。
 *
 * 后端按字段级权限门控（看不到金额的角色会 403），所以调用方要兜 403 ——
 * 不是所有有推广读权限的人都能看这个。
 */
export async function promotionAmountLog(
  promotionId: string,
  limit = 100
): Promise<PromotionAmountLog[]> {
  const resp = await apiClient.get<PromotionAmountLog[]>(
    `/api/promotions/${promotionId}/amount-log`,
    { params: { limit } }
  );
  return resp.data;
}
