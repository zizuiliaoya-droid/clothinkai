// U03 blogger feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  Blogger,
  BloggerCreate,
  BloggerListFilters,
  BloggerMissingTags,
  BloggerPage,
  BloggerTagCreate,
  BloggerTagDict,
  BloggerTagItem,
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

// 8b-3 标签字典：读用 blogger:read，增删要 blogger_tag:write（主管、管理员）

export async function listBloggerTags(): Promise<BloggerTagDict> {
  const resp = await apiClient.get<BloggerTagDict>("/api/blogger-tags");
  return resp.data;
}

export async function createBloggerTag(
  payload: BloggerTagCreate
): Promise<BloggerTagItem> {
  const resp = await apiClient.post<BloggerTagItem>("/api/blogger-tags", payload);
  return resp.data;
}

export async function deleteBloggerTag(tagId: string): Promise<void> {
  await apiClient.delete(`/api/blogger-tags/${tagId}`);
}

/** 导入缺的标签；不传 batchId 取最近一个手工博主导入批次。 */
export async function listMissingBloggerTags(
  batchId?: string
): Promise<BloggerMissingTags> {
  const resp = await apiClient.get<BloggerMissingTags>(
    "/api/blogger-tags/missing",
    { params: batchId ? { batch_id: batchId } : {} }
  );
  return resp.data;
}
