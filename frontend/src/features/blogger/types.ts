// U03 blogger feature 类型定义。

import type { Platform } from "@/features/common/platforms";

export type { Platform };
export type BloggerType = "素人" | "KOC" | "KOL" | "明星";
export type GenderTarget = "女性" | "男性" | "中性";

/**
 * 博主响应。
 *
 * 注意：quote / wechat / phone 由后端按角色过滤；
 * 敏感字段由后端字段级权限矩阵过滤，无权角色收到 null。
 */
export interface Blogger {
  id: string;
  xiaohongshu_id: string;
  nickname: string;
  platform: string;
  level?: string | null;
  content_category?: string | null;
  contact_primary?: string | null;
  contact_primary_added?: boolean;
  contact_backup?: string | null;
  contact_backup_added?: boolean;
  is_added_success?: boolean;
  wechat: string | null;
  phone: string | null;
  follower_count: number | null;
  blogger_type: string | null;
  gender_target: string | null;
  category_tags: string[];
  quality_tags: string[];
  quote: string | null;
  cooperation_history: string | null;
  remark: string | null;
  is_suspected_fake: boolean;
  is_active: boolean;
  is_deleted: boolean;
  crawler_metrics?: Record<string, unknown>;
  // 8b：抖音网页ID、主页链接、灰豚抖音统计快照、报价备注（与报价同一条字段权限，看不到收到 null）
  web_id?: string | null;
  homepage_url?: string | null;
  platform_metrics?: BloggerPlatformMetrics | null;
  quote_note?: string | null;
  created_at: string;
  updated_at: string;
}

/** 灰豚抖音导入写的统计快照：raw 是原文，values 是解析成功的数值（只展示不计算）。 */
export interface BloggerPlatformMetrics {
  source?: string;
  raw?: Record<string, string>;
  values?: Record<string, number>;
}

export interface BloggerCreate {
  xiaohongshu_id: string;
  nickname: string;
  platform?: Platform;
  level?: string | null;
  content_category?: string | null;
  contact_primary?: string | null;
  contact_primary_added?: boolean;
  contact_backup?: string | null;
  contact_backup_added?: boolean;
  wechat?: string | null;
  phone?: string | null;
  follower_count?: number | null;
  blogger_type?: BloggerType | null;
  gender_target?: GenderTarget | null;
  category_tags?: string[];
  quality_tags?: string[];
  quote?: string | null;
  cooperation_history?: string | null;
  remark?: string | null;
  is_suspected_fake?: boolean;
  web_id?: string | null;
  homepage_url?: string | null;
  quote_note?: string | null;
}

export interface BloggerUpdate {
  xiaohongshu_id?: string;
  nickname?: string;
  platform?: Platform;
  level?: string | null;
  content_category?: string | null;
  contact_primary?: string | null;
  contact_primary_added?: boolean;
  contact_backup?: string | null;
  contact_backup_added?: boolean;
  wechat?: string | null;
  phone?: string | null;
  follower_count?: number | null;
  blogger_type?: BloggerType | null;
  gender_target?: GenderTarget | null;
  category_tags?: string[];
  quality_tags?: string[];
  quote?: string | null;
  cooperation_history?: string | null;
  remark?: string | null;
  is_suspected_fake?: boolean;
  is_active?: boolean;
  web_id?: string | null;
  homepage_url?: string | null;
  quote_note?: string | null;
}

export interface BloggerPage {
  items: Blogger[];
  total: number;
  page: number;
  page_size: number;
}

export interface BloggerListFilters {
  page?: number;
  page_size?: number;
  keyword?: string;
  blogger_type?: string;
  level?: string;
  follower_count_min?: number;
  follower_count_max?: number;
  category_tag?: string;
  quality_tag?: string;
  platform?: string;
  is_suspected_fake?: boolean;
  is_active?: boolean;
  include_inactive?: boolean;
  recent_growth_only?: boolean;
}

// 8b-3 标签字典（GET /api/blogger-tags）
export interface BloggerTagItem {
  id: string;
  value: string;
  sort_order: number;
}

export interface BloggerTagDict {
  items: BloggerTagItem[];
  /** 系统自动计算的质量标签，只读 */
  system_tags: string[];
  /** 能否增删字典项（= 持有 blogger_tag:write）；前端据此显隐，不硬编码角色 */
  can_manage: boolean;
}

export interface BloggerTagCreate {
  value: string;
  sort_order?: number;
}

/** 导入缺的标签（GET /api/blogger-tags/missing）：batch_id 为 null = 没有看得到的博主导入批次。 */
export interface BloggerMissingTags {
  batch_id: string | null;
  items: { tag: string; count: number; rows: number[] }[];
}
