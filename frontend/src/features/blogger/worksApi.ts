import { apiClient } from "@/services/apiClient";

export interface WorkInput {
  work_id?: string | null;
  work_url?: string | null;
  title?: string | null;
  published_on: string;
  collected_at: string;
  source: string;
  like_count?: number | string | null;
  comment_count?: number | string | null;
  share_count?: number | string | null;
  collect_count?: number | string | null;
  play_count?: number | string | null;
}

export interface WorkItem extends WorkInput {
  id: string;
  platform: string;
  created_by_name: string | null;
  import_batch_id: string | null;
  row_number: number | null;
  interaction_total: number | null;
  collect_like_ratio: number | null;
  share_like_ratio: number | null;
  comment_like_ratio: number | null;
}

export interface WorkPage {
  items: WorkItem[];
  total: number;
  page: number;
  page_size: number;
  can_write: boolean;
  can_read_source: boolean;
}

export async function listWorks(bloggerId: string, filters: { page?: number; date_from?: string; date_to?: string; snapshot_id?: string } = {}) {
  return (await apiClient.get<WorkPage>(`/api/bloggers/${bloggerId}/works`, { params: filters })).data;
}

export async function saveWork(bloggerId: string, payload: WorkInput) {
  return (await apiClient.post<{ id: string; inserted: boolean }>(`/api/bloggers/${bloggerId}/works`, payload)).data;
}

export async function getWorkSource(bloggerId: string, snapshotId: string) {
  return (await apiClient.get<{ url: string }>(`/api/bloggers/${bloggerId}/works/${snapshotId}/source`)).data;
}
