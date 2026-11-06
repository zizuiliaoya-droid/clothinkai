import { apiClient } from "@/services/apiClient";

export interface WorkWechat { id: string; value: string; is_active: boolean }
export interface ClaimSettings { enabled_at: string | null; unclaimed_count: number; pending_work_wechat_count: number; can_enable: boolean }
export interface BloggerClaim {
  id: string; blogger_id: string; pr_id: string; pr_name: string; method: string;
  operated_by: string; created_at: string; work_wechat_id: string | null;
  work_wechat_name: string | null; work_wechat_active: boolean | null;
}
export async function getClaimSettings(): Promise<ClaimSettings> {
  return (await apiClient.get<ClaimSettings>("/api/bloggers/claim-settings")).data;
}
export async function enableClaims(): Promise<ClaimSettings> {
  return (await apiClient.post<ClaimSettings>("/api/bloggers/claim-settings/enable")).data;
}
export async function listWorkWechats(): Promise<{items: WorkWechat[]; can_manage: boolean}> {
  return (await apiClient.get<{items: WorkWechat[]; can_manage: boolean}>("/api/bloggers/work-wechats")).data;
}
export async function createWorkWechat(value: string): Promise<WorkWechat> {
  return (await apiClient.post<WorkWechat>("/api/bloggers/work-wechats", {value})).data;
}
export async function setWorkWechatActive(id: string, is_active: boolean): Promise<WorkWechat> {
  return (await apiClient.patch<WorkWechat>(`/api/bloggers/work-wechats/${id}`, {is_active})).data;
}
export async function listClaimPrOptions(): Promise<{id: string; name: string}[]> {
  return (await apiClient.get<{id: string; name: string}[]>("/api/bloggers/claim-pr-options")).data;
}
export async function listClaims(id: string): Promise<BloggerClaim[]> {
  return (await apiClient.get<BloggerClaim[]>(`/api/bloggers/${id}/claims`)).data;
}
export async function selfClaim(items: {blogger_id: string; wechat: string; work_wechat_id: string}[]) {
  return (await apiClient.post<{blogger_id: string; status: "claimed" | "already_claimed" | "not_verified"}[]>("/api/bloggers/claims/self", {items})).data;
}
export async function assignClaims(items: {blogger_id: string; pr_id: string; work_wechat_id: string}[]) {
  return (await apiClient.post<{assigned: number; existing: number}>("/api/bloggers/claims/assign", {items})).data;
}
export async function revokeClaim(bloggerId: string, prId: string) {
  await apiClient.delete(`/api/bloggers/${bloggerId}/claims/${prId}`);
}
export async function switchClaimWechat(bloggerId: string, prId: string, work_wechat_id: string) {
  await apiClient.patch(`/api/bloggers/${bloggerId}/claims/${prId}`, {work_wechat_id});
}
export async function transferClaim(bloggerId: string, prId: string, to_pr_id: string, work_wechat_id: string) {
  await apiClient.post(`/api/bloggers/${bloggerId}/claims/${prId}/transfer`, {to_pr_id, work_wechat_id});
}
