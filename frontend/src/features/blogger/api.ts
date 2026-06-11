// U03 blogger feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  Blogger,
  BloggerCreate,
  BloggerListFilters,
  BloggerPage,
  BloggerUpdate,
} from "./types";

export async function listBloggers(
  filters: BloggerListFilters = {}
): Promise<BloggerPage> {
  const resp = await apiClient.get<BloggerPage>("/api/bloggers/", {
    params: filters,
  });
  return resp.data;
}

export async function getBlogger(bloggerId: string): Promise<Blogger> {
  const resp = await apiClient.get<Blogger>(`/api/bloggers/${bloggerId}`);
  return resp.data;
}

export async function createBlogger(payload: BloggerCreate): Promise<Blogger> {
  const resp = await apiClient.post<Blogger>("/api/bloggers/", payload);
  return resp.data;
}

export async function updateBlogger(
  bloggerId: string,
  payload: BloggerUpdate
): Promise<Blogger> {
  const resp = await apiClient.put<Blogger>(
    `/api/bloggers/${bloggerId}`,
    payload
  );
  return resp.data;
}

export async function deleteBlogger(bloggerId: string): Promise<void> {
  await apiClient.delete(`/api/bloggers/${bloggerId}`);
}

export async function disableBlogger(bloggerId: string): Promise<Blogger> {
  const resp = await apiClient.post<Blogger>(
    `/api/bloggers/${bloggerId}/disable`
  );
  return resp.data;
}

export async function restoreBlogger(bloggerId: string): Promise<Blogger> {
  const resp = await apiClient.post<Blogger>(
    `/api/bloggers/${bloggerId}/restore`
  );
  return resp.data;
}
