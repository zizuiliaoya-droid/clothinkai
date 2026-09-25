// 页面筛选记忆（PRD 第 7 章 user_filter_pref，后端复用 user_preference 表）。

import { apiClient } from "@/services/apiClient";

/** 允许记忆筛选的页面编码，需与后端 FILTER_PAGE_CODES 白名单一致。 */
export type FilterPageCode =
  | "product_roi"
  | "pr_work_progress"
  | "store_daily"
  | "bi_dashboard";

export async function getFilterPreference<T extends object>(
  pageCode: FilterPageCode,
): Promise<Partial<T>> {
  const resp = await apiClient.get<Partial<T>>(
    `/api/preferences/filters/${pageCode}`,
  );
  return resp.data;
}

export async function saveFilterPreference<T extends object>(
  pageCode: FilterPageCode,
  filters: T,
): Promise<void> {
  await apiClient.put(`/api/preferences/filters/${pageCode}`, filters);
}
