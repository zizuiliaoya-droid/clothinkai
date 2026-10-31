import type { UserSummary } from "@/types";

export function isPlatformSession(user: UserSummary | null) {
  return user?.session_mode === "platform" ||
    (user?.session_mode == null && user?.roles.includes("platform_admin")) === true;
}

export const SUPPORT_PAGES = [
  { path: "/goods", label: "商品 / 款式" },
  { path: "/bloggers", label: "博主库" },
  { path: "/promotions", label: "站外推广" },
  { path: "/settlements", label: "财务结款" },
  { path: "/work-progress", label: "工作进度表" },
  { path: "/publish-progress", label: "发文进度表" },
  { path: "/production", label: "单品投产" },
  { path: "/store/daily", label: "店铺日明细" },
  { path: "/store/weekly", label: "店铺周汇总" },
  { path: "/store/monthly", label: "店铺月度汇总" },
  { path: "/bi", label: "BI看板" },
  { path: "/users", label: "用户与权限" },
] as const;

export function isSupportPage(pathname: string) {
  return SUPPORT_PAGES.some((page) => page.path === pathname);
}
