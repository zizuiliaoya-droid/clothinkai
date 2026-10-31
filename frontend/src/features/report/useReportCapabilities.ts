// 报表能力（7e R1，报表视图设计 5.2）：菜单「工作进度表」「BI看板」与导出按钮按它显示。
// 只管显示，后端 403 才是闸门。

import { useQuery } from "@tanstack/react-query";
import { useAuthStore } from "@/stores/authStore";
import { getReportCapabilities, type ReportExportType } from "./api";
import type { ReportCapabilities } from "./types";
import { useSupportStore } from "@/stores/supportStore";

/** 与后端权限缓存 TTL 相同：改了授权最多 5 分钟后菜单跟上（刷新页面立即生效）。 */
const CAPABILITIES_STALE_MS = 5 * 60 * 1000;

/**
 * 当前用户的报表能力。未登录不查；加载中 `data` 为 undefined，调用方按「没有」处理（不先显示再收回）。
 *
 * 查询键带用户 id：退出后换账号登录不会沿用上一个人的能力。
 */
export function useReportCapabilities() {
  const userId = useAuthStore((s) => (s.isAuthenticated ? s.user?.id ?? null : null));
  const support = useSupportStore((s) => s.session);
  return useQuery({
    queryKey: ["report-capabilities", userId, support?.key ?? null],
    queryFn: getReportCapabilities,
    enabled: userId != null,
    staleTime: CAPABILITIES_STALE_MS,
    select: (data) => support ? { ...data, export: [], store_write: false, refresh: false } : data,
  });
}

/** 能否导出某张报表：能力没回来前一律 false。 */
export function canExportReport(
  caps: ReportCapabilities | undefined,
  type: ReportExportType
): boolean {
  return caps?.export.includes(type) ?? false;
}
