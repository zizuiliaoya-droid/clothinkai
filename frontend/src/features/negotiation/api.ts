// 谈款审核 API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  BloggerCooperationHistory,
  Negotiation,
  NegotiationCreate,
  NegotiationFilters,
  NegotiationPage,
  NegotiationReviewRequest,
  NegotiationUpdate,
} from "./types";

export async function listNegotiations(
  filters: NegotiationFilters = {}
): Promise<NegotiationPage> {
  const resp = await apiClient.get<NegotiationPage>("/api/negotiations/", {
    params: filters,
  });
  return resp.data;
}

export async function getNegotiation(id: string): Promise<Negotiation> {
  const resp = await apiClient.get<Negotiation>(`/api/negotiations/${id}`);
  return resp.data;
}

export async function createNegotiation(
  payload: NegotiationCreate
): Promise<Negotiation> {
  const resp = await apiClient.post<Negotiation>("/api/negotiations/", payload);
  return resp.data;
}

export async function updateNegotiation(
  id: string,
  payload: NegotiationUpdate
): Promise<Negotiation> {
  const resp = await apiClient.put<Negotiation>(
    `/api/negotiations/${id}`,
    payload
  );
  return resp.data;
}

/** 提交审核。会清掉上一轮的驳回意见。 */
export async function submitNegotiation(id: string): Promise<Negotiation> {
  const resp = await apiClient.post<Negotiation>(
    `/api/negotiations/${id}/submit`
  );
  return resp.data;
}

/** 主管审核。通过会同时生成推广单；驳回必须带审核意见。 */
export async function reviewNegotiation(
  id: string,
  payload: NegotiationReviewRequest
): Promise<Negotiation> {
  const resp = await apiClient.post<Negotiation>(
    `/api/negotiations/${id}/review`,
    payload
  );
  return resp.data;
}

/** 各状态单据数，给 Tab 做角标。 */
export async function negotiationStatusCounts(): Promise<
  Record<string, number>
> {
  const resp = await apiClient.get<Record<string, number>>(
    "/api/negotiations/status-counts"
  );
  return resp.data;
}

/** 博主最近 N 次合作款式（取自推广单，不含草稿谈款）。 */
export async function bloggerCooperationHistory(
  bloggerId: string,
  limit = 5
): Promise<BloggerCooperationHistory> {
  const resp = await apiClient.get<BloggerCooperationHistory>(
    `/api/negotiations/blogger/${bloggerId}/history`,
    { params: { limit } }
  );
  return resp.data;
}
