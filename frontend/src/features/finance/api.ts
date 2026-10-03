// U05 finance feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  DailySummaryActivityResponse,
  DailySummaryAsOfResponse,
  Settlement,
  SettlementExtraItemCreateRequest,
  SettlementListFilters,
  SettlementPage,
  SettlementPaymentAmountRequest,
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

/** 付款截图允许的格式与大小（与后端 check_image_payload 一致）。 */
export const PAYMENT_PROOF_MIME_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
export const PAYMENT_PROOF_MAX_BYTES = 10 * 1024 * 1024;

/**
 * 上传付款截图并标记已付款：后端代传到私有桶，一个请求完成。
 *
 * 不再由浏览器直传 R2 —— 直传要求 bucket 配 CORS，生产私有桶没配，浏览器只会报
 * 「Failed to fetch」。与收款码、催发截图、7 天数据截图同一套做法。
 */
export async function uploadPaymentProof(
  settlementId: string,
  paymentDate: string,
  file: File
): Promise<Settlement> {
  const form = new FormData();
  form.append("payment_date", paymentDate);
  form.append("file", file, file.name);
  const resp = await apiClient.post<Settlement>(
    `/api/settlements/${settlementId}/payment-proof/upload`,
    form,
    // 截图最大 10MB，慢网下 30 秒的默认超时不够
    { timeout: 120_000 }
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

// ---------------------------------------------------------------------------
// 拍单 / 刷单 / 余额（U16）
// ---------------------------------------------------------------------------

import type {
  BalanceRecord,
  BrushingCreate,
  OrderAdjustment,
  OrderAdjustmentFilters,
  OrderAdjustmentPage,
} from "./types";

export async function listOrderAdjustments(
  params: OrderAdjustmentFilters = {},
): Promise<OrderAdjustmentPage> {
  const resp = await apiClient.get<OrderAdjustmentPage>(
    "/api/finance/order-adjustments",
    { params }
  );
  return resp.data;
}

/** 上传博主收款码（后端代传到私有桶）。 */
export async function uploadOrderPaymentQr(
  rowId: string,
  file: File,
): Promise<OrderAdjustment> {
  const body = new FormData();
  body.append("image", file, file.name);
  const resp = await apiClient.post<OrderAdjustment>(
    `/api/finance/order-adjustments/${rowId}/payment-qr/upload`,
    body,
  );
  return resp.data;
}

export async function removeOrderPaymentQr(rowId: string): Promise<void> {
  await apiClient.delete(
    `/api/finance/order-adjustments/${rowId}/payment-qr`,
  );
}

export async function createBrushing(
  payload: BrushingCreate
): Promise<unknown> {
  const resp = await apiClient.post("/api/finance/order-adjustments/brushing", payload);
  return resp.data;
}

export async function listBalanceRecords(params: {
  date_from?: string;
  date_to?: string;
} = {}): Promise<BalanceRecord[]> {
  const resp = await apiClient.get<BalanceRecord[]>(
    "/api/finance/balance-records",
    { params }
  );
  return resp.data;
}
