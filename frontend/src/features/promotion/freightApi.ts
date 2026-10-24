import type { FreightFilters, FreightPage, ReturnFreightPayment } from "./freightTypes";
import { apiClient } from "@/services/apiClient";

export const FREIGHT_PAYMENTS_KEY = ["return-freight-payments"] as const;
export const freightDetailKey = (id: string) => [...FREIGHT_PAYMENTS_KEY, "detail", id] as const;

const base = "/api/return-freight-payments";

export async function listFreightPayments(filters: FreightFilters = {}): Promise<FreightPage> {
  return (await apiClient.get<FreightPage>(`${base}/`, { params: filters })).data;
}
export async function getFreightPayment(id: string): Promise<ReturnFreightPayment> {
  return (await apiClient.get<ReturnFreightPayment>(`${base}/${id}`)).data;
}
export async function submitFreightPayment(id: string, amount?: string): Promise<ReturnFreightPayment> {
  return (await apiClient.post<ReturnFreightPayment>(`/api/promotions/${id}/return-freight`, amount === undefined ? {} : { amount })).data;
}
export async function reviewFreightPayment(id: string, payload: { action: "approve" | "reject"; reason?: string }): Promise<ReturnFreightPayment> {
  return (await apiClient.post<ReturnFreightPayment>(`${base}/${id}/review`, payload)).data;
}
export async function resubmitFreightPayment(id: string, payload: { note: string; amount?: string }): Promise<ReturnFreightPayment> {
  return (await apiClient.post<ReturnFreightPayment>(`${base}/${id}/resubmit`, payload)).data;
}
export async function rejectFreightPayment(id: string, reason: string): Promise<ReturnFreightPayment> {
  return (await apiClient.post<ReturnFreightPayment>(`${base}/${id}/reject-payment`, { reason })).data;
}
export async function payFreightPayment(id: string, file: File): Promise<ReturnFreightPayment> {
  const form = new FormData();
  form.append("file", file, file.name);
  return (await apiClient.post<ReturnFreightPayment>(`${base}/${id}/pay`, form, { timeout: 120_000 })).data;
}
