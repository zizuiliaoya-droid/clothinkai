import { apiClient } from "@/services/apiClient";

export const audienceDimensions = { province: "地域省份", city_tier: "城市级别", age: "年龄", gender: "性别" } as const;
export interface AudienceEntry { label: string; percentage?: number | string | null; tgi?: number | string | null }
export interface AudienceInput {
  dimension: keyof typeof audienceDimensions;
  collected_on: string;
  source: string;
  completeness: "complete" | "partial";
  estimated: boolean;
  tgi_source?: string | null;
  entries: AudienceEntry[];
}
export interface AudienceItem extends AudienceInput {
  id: string;
  import_batch_id: string | null;
  row_numbers: number[];
  created_by_name: string | null;
  is_current: boolean;
}
export interface AudiencePage {
  items: AudienceItem[]; total: number; page: number; page_size: number;
  can_write: boolean; can_read_source: boolean;
}
export async function listAudience(bloggerId: string, history = false, page = 1) {
  return (await apiClient.get<AudiencePage>(`/api/bloggers/${bloggerId}/audience`, { params: { history, page } })).data;
}
export async function saveAudience(bloggerId: string, payload: AudienceInput) {
  return (await apiClient.post<{ id: string; inserted: boolean; current: boolean; message: string }>(`/api/bloggers/${bloggerId}/audience`, payload)).data;
}
export async function getAudienceSource(bloggerId: string, captureId: string) {
  return (await apiClient.get<{ url: string }>(`/api/bloggers/${bloggerId}/audience/${captureId}/source`)).data;
}
