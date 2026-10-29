import { apiClient } from "@/services/apiClient";

export interface ColumnPreference { order: string[]; hidden: string[] }
export interface ColumnOption { key: string; label: string }
export const DEFAULT_COLUMN_PREFERENCE: ColumnPreference = { order: [], hidden: [] };

export async function getPromotionColumnPreference(): Promise<ColumnPreference> {
  return (await apiClient.get<ColumnPreference>("/api/preferences/columns/promotion_list")).data;
}

export async function savePromotionColumnPreference(preference: ColumnPreference): Promise<void> {
  await apiClient.put("/api/preferences/columns/promotion_list", preference);
}

/** 输入必须已经过页面字段权限过滤，偏好只做取舍和排序，不能补回任何列。 */
export function orderedColumnKeys(available: readonly string[], preference: ColumnPreference): string[] {
  return [...new Set([...preference.order.filter((key) => available.includes(key)), ...available])];
}

export function visibleColumnKeys(available: readonly string[], preference: ColumnPreference): string[] {
  const ordered = orderedColumnKeys(available, preference);
  const visible = ordered.filter((key) => !preference.hidden.includes(key));
  return visible.length ? visible : ordered.slice(0, 1);
}
