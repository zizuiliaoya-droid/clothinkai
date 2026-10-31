import { apiClient } from "@/services/apiClient";

export interface TenantItem {
  id: string;
  code: string;
  name: string;
  status: string;
  created_at: string;
}
export interface TenantCreate {
  code: string;
  name: string;
  admin_username: string;
}
export interface TenantCreated {
  tenant: TenantItem;
  admin_username: string;
  initial_password: string;
  must_change_password: boolean;
}
export async function listTenants(page: number) {
  const result = await apiClient.get<{ items: TenantItem[]; meta: { page: number; page_size: number; total: number } }>("/api/tenants", { params: { page, page_size: 20 } });
  return result.data;
}
export async function createTenant(payload: TenantCreate): Promise<TenantCreated> {
  return (await apiClient.post<TenantCreated>("/api/tenants", payload)).data;
}
export async function setTenantStatus(id: string, status: "active" | "disabled") {
  return (await apiClient.put<TenantItem>(`/api/tenants/${id}/status`, { status })).data;
}
