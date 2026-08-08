// U03 blogger feature 类型定义。

export type BloggerType = "素人" | "KOC" | "KOL" | "明星";
export type Platform = "小红书" | "抖音" | "快手" | "B站";
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
  created_at: string;
  updated_at: string;
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
