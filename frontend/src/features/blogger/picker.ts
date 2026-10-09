// 博主下拉（BloggerSelect）的纯函数：缓存键、请求参数、选项文字（8b §7.3）。

import type { Blogger, BloggerListFilters } from "./types";

/**
 * React Query key 前缀：按平台分开缓存（谈款页换平台不串结果），没传平台记 "*"。
 * 存的是选项数组；不要和 BloggerListPage 的 ["bloggers", 筛选条件]（分页原始数据）共用 key。
 */
export function bloggerPickerQueryKey(platform?: string): readonly unknown[] {
  return ["bloggers", "picker-options", platform || "*"] as const;
}

/** 搜索请求参数；没传平台时不带 platform（推广单页行为同现在）。 */
export function bloggerPickerParams(
  keyword: string | undefined,
  platform: string | undefined,
  pageSize: number
): BloggerListFilters {
  const params: BloggerListFilters = { page: 1, page_size: pageSize, keyword };
  if (platform) params.platform = platform;
  return params;
}

/** 选项文字「昵称（平台·账号）」：账号历史原因叫 xiaohongshu_id，抖音存的是博主ID。 */
export function bloggerOptionLabel(
  b: Pick<Blogger, "nickname" | "platform" | "xiaohongshu_id">
): string {
  return `${b.nickname}（${b.platform}·${b.xiaohongshu_id}）`;
}
