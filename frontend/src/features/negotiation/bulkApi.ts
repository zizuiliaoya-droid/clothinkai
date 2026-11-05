import { apiClient } from "@/services/apiClient";
import type { Negotiation, NegotiationCreate, NegotiationFinalReviewRequest, NegotiationReceiverConfirm, NegotiationReviewRequest } from "./types";

export type BulkAction = "submit" | "review" | "final-review" | "confirm-receiver";
export type BulkActionPayload = NegotiationReviewRequest | NegotiationFinalReviewRequest | NegotiationReceiverConfirm | Record<string, never>;
export interface BulkResult {
  row_key: string;
  negotiation_id: string | null;
  status: "success" | "skipped" | "failed";
  code: string;
  message: string;
  reused: boolean;
  negotiation: Negotiation | null;
}
export interface BulkResponse { results: BulkResult[]; success: number; skipped: number; failed: number }
export interface BulkCreateItem { client_create_key: string; payload: NegotiationCreate }
export const BULK_LIMIT = 50;
export const BULK_ACTION_LABEL: Record<BulkAction, string> = {submit: "提交审核", review: "主管审核", "final-review": "老板审核", "confirm-receiver": "确认收货"};
export const BULK_ACTION_UI: Record<BulkAction, string> = {submit: "submit", review: "manager_review", "final-review": "final_review", "confirm-receiver": "receiver_confirm"};
export async function bulkCreate(creationGroupId: string, items: BulkCreateItem[]): Promise<BulkResponse> {
  return (await apiClient.post<BulkResponse>("/api/negotiations/bulk/create", {creation_group_id: creationGroupId, items}, {timeout: 120_000})).data;
}
export async function lookupBulkCreates(keys: string[]): Promise<BulkResponse> {
  return (await apiClient.post<BulkResponse>("/api/negotiations/bulk/lookup", {client_create_keys: keys})).data;
}
export async function bulkAction(action: BulkAction, items: {negotiation_id: string; payload: BulkActionPayload}[]): Promise<BulkResponse> {
  return (await apiClient.post<BulkResponse>(`/api/negotiations/bulk/${action}`, {items}, {timeout: 120_000})).data;
}
