// 只保存五个模块的地址，不缓存页面、表单或业务数据。
export const MODULES = [
  { path: "/bloggers", label: "博主库", queries: ["bloggers", "blogger-tags"] },
  { path: "/negotiations", label: "谈款审核", queries: ["negotiations", "negotiation", "negotiation-status-counts", "line-events"] },
  { path: "/promotions", label: "站外推广", queries: ["promotions", "line-events", "promotion-urge-records"] },
  { path: "/urge-tasks", label: "催发任务", queries: ["urge-tasks", "urge-task", "urge-dashboard"] },
  { path: "/production", label: "投产报表", queries: ["production", "production-trend"] },
] as const;

export interface ModuleTab { path: string; href: string }

export function moduleFor(path: string) {
  return MODULES.find((module) => module.path === path);
}

/** 仅识别五个页面的主列表键；共享前缀的选项/详情不代表模块授权。 */
export function moduleForListQuery(key: readonly unknown[]) {
  const module = MODULES.find((item) => item.path.slice(1) === key[0]);
  if (!module) return undefined;
  if (module.path === "/production") {
    // ProductionPage: [production, preset, dateFrom, dateTo, excludeBrushing, seasons]
    return key.length === 6 && typeof key[1] === "string" &&
      (key[2] === undefined || typeof key[2] === "string") &&
      (key[3] === undefined || typeof key[3] === "string") &&
      typeof key[4] === "boolean" && Array.isArray(key[5]) &&
      key[5].every((season) => typeof season === "string") ? module : undefined;
  }
  // Blogger/Negotiation/Promotion/UrgePage: [namespace, filtersObject]
  return key.length === 2 && key[1] !== null && typeof key[1] === "object" &&
    !Array.isArray(key[1]) ? module : undefined;
}

// User.id 是全局 UUID 主键，每条用户记录只属于一个租户；无需读取 token。
export function navigationStorageKey(userId: string) {
  return `clothing_erp_module_tabs:${userId}`;
}

export function readTabs(userId: string, roles: string): ModuleTab[] {
  try {
    const value = JSON.parse(localStorage.getItem(navigationStorageKey(userId)) ?? "null");
    if (value?.roles !== roles || !Array.isArray(value.tabs)) return [];
    const tabs: ModuleTab[] = [];
    for (const tab of value.tabs) {
      if (!tab || !moduleFor(tab.path) || typeof tab.href !== "string") continue;
      // 严格限本地已知路由，保留原查询串/fragment，不接受外部或协议地址。
      if (tab.href.split(/[?#]/, 1)[0] !== tab.path) continue;
      if (!tabs.some((saved) => saved.path === tab.path)) tabs.push({ path: tab.path, href: tab.href });
    }
    return tabs;
  } catch {
    return [];
  }
}

export function saveTabs(userId: string, roles: string, tabs: ModuleTab[]) {
  try {
    localStorage.setItem(navigationStorageKey(userId), JSON.stringify({ roles, tabs }));
  } catch {
    // 浏览器禁用存储时仍能使用本次会话页签。
  }
}
