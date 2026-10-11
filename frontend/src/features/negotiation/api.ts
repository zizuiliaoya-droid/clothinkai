// 谈款审核 API 调用层（流程线设计 7.2）。

import { apiClient } from "@/services/apiClient";
import type {
  BloggerCooperationHistory,
  Negotiation,
  NegotiationCreate,
  NegotiationFilters,
  NegotiationFinalReviewRequest,
  NegotiationGoodsMembers,
  NegotiationPage,
  NegotiationReceiverConfirm,
  NegotiationReceiverUpdate,
  NegotiationReviewRequest,
  NegotiationUpdate,
  OthersInProgressItem,
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

/** 新建（N1）：落草稿；响应带 others_in_progress。 */
export async function createNegotiation(
  payload: NegotiationCreate
): Promise<Negotiation> {
  const resp = await apiClient.post<Negotiation>("/api/negotiations/", payload);
  return resp.data;
}

/** 编辑（N2）：草稿 / 老板退回、本人；改完状态不变。 */
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

/** 提交（N3）→ 主管待审。缺项 422 FLOW_GATE_MISSING；响应带 others_in_progress。 */
export async function submitNegotiation(id: string): Promise<Negotiation> {
  const resp = await apiClient.post<Negotiation>(
    `/api/negotiations/${id}/submit`
  );
  return resp.data;
}

/** 主管审核（N4 / N5）：通过 → 待老板审核（不建单）；驳回 → 审批未通过（终态），意见必填。 */
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

/** 老板审核（N6 / N7）：定稿 → 待补收货地址；退回 → 老板退回，意见必填。 */
export async function finalReviewNegotiation(
  id: string,
  payload: NegotiationFinalReviewRequest
): Promise<Negotiation> {
  const resp = await apiClient.post<Negotiation>(
    `/api/negotiations/${id}/final-review`,
    payload
  );
  return resp.data;
}

/** 保存收货信息（N8）：只改传了的项。 */
export async function saveNegotiationReceiver(
  id: string,
  payload: NegotiationReceiverUpdate
): Promise<Negotiation> {
  const resp = await apiClient.put<Negotiation>(
    `/api/negotiations/${id}/receiver`,
    payload
  );
  return resp.data;
}

/** 确认收货信息（N9）→ 已定稿并生成推广单；重复确认 409。 */
export async function confirmNegotiationReceiver(
  id: string,
  payload: NegotiationReceiverConfirm
): Promise<Negotiation> {
  const resp = await apiClient.post<Negotiation>(
    `/api/negotiations/${id}/confirm-receiver`,
    payload
  );
  return resp.data;
}

/** 复制为新谈款（N10）：只在审批未通过、本人；返回新草稿。 */
export async function copyNegotiation(id: string): Promise<Negotiation> {
  const resp = await apiClient.post<Negotiation>(
    `/api/negotiations/${id}/copy`
  );
  return resp.data;
}

/** 各状态单据数（按可见范围），给 Tab 做角标；另有「主管待审超时」。 */
export async function negotiationStatusCounts(): Promise<
  Record<string, number>
> {
  const resp = await apiClient.get<Record<string, number>>(
    "/api/negotiations/status-counts"
  );
  return resp.data;
}

/** 博主最近 N 次合作（取自推广单）+ 最近 5 条谈款驳回记录。 */
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

/** 同一博主有别的 PR 在谈（只提示不拦）。编辑时传当前单 id 排除自己。 */
export async function othersInProgress(
  bloggerId: string,
  excludeNegotiationId?: string
): Promise<OthersInProgressItem[]> {
  const resp = await apiClient.get<OthersInProgressItem[]>(
    `/api/negotiations/blogger/${bloggerId}/others-in-progress`,
    {
      params: excludeNegotiationId
        ? { exclude_negotiation_id: excludeNegotiationId }
        : undefined,
    }
  );
  return resp.data;
}

/** 新建 / 换商品时明细该有哪几行；不传商品时后端按款式推定。 */
export async function negotiationGoodsMembers(
  styleId: string,
  goodsMainId?: string | null
): Promise<NegotiationGoodsMembers> {
  const resp = await apiClient.get<NegotiationGoodsMembers>(
    "/api/negotiations/goods-members",
    {
      params: goodsMainId
        ? { style_id: styleId, goods_main_id: goodsMainId }
        : { style_id: styleId },
    }
  );
  return resp.data;
}
