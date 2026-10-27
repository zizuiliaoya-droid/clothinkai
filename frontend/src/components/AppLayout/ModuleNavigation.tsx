import { useEffect, useRef, useState } from "react";
import { Button } from "antd";
import { CloseOutlined } from "@ant-design/icons";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { isForbiddenError } from "@/features/warehouse/shipmentForm";
import { moduleFor, moduleForListQuery, readTabs, saveTabs } from "./moduleTabsStorage";
import "./moduleNavigation.css";

export function ModuleNavigation({ productionAccess }: { productionAccess?: boolean }) {
  const user = useAuthStore((state) => state.isAuthenticated ? state.user : null);
  if (!user) return null;
  const roles = JSON.stringify([...user.roles].sort());
  const warehouseOnly = user.roles.includes("warehouse") &&
    !user.roles.some((role) => role === "admin" || role === "platform_admin");
  return <ModuleTabs key={`${user.id}:${roles}`} userId={user.id} roles={roles}
    productionAccess={productionAccess} warehouseOnly={warehouseOnly} />;
}

function ModuleTabs({ userId, roles, productionAccess, warehouseOnly }: {
  userId: string; roles: string; productionAccess?: boolean; warehouseOnly: boolean;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const client = useQueryClient();
  const [tabs, setTabs] = useState(() => readTabs(userId, roles));
  const [denied, setDenied] = useState<string[]>([]);
  const activeLink = useRef<HTMLAnchorElement>(null);
  const path = location.pathname;
  const href = path + location.search + location.hash;

  useEffect(() => {
    setTabs((previous) => {
      const next = previous.filter((tab) => !warehouseOnly && !denied.includes(tab.path) &&
        !(tab.path === "/production" && productionAccess === false));
      const current = moduleFor(path);
      if (current && !warehouseOnly && !denied.includes(path) &&
          !(path === "/production" && productionAccess === false)) {
        const index = next.findIndex((tab) => tab.path === path);
        if (index < 0) next.push({ path, href });
        else next[index] = { path, href };
      }
      return JSON.stringify(next) === JSON.stringify(previous) ? previous : next;
    });
  }, [path, href, productionAccess, warehouseOnly, denied]);

  useEffect(() => { saveTabs(userId, roles, tabs); }, [userId, roles, tabs]);

  // 页面不保活。切换路由时刷新该模块已有查询，包括30秒内仍被视为新鲜的缓存。
  useEffect(() => {
    const current = moduleFor(path);
    if (!current) return;
    void client.invalidateQueries({
      predicate: (query) => (current.queries as readonly unknown[]).includes(query.queryKey[0]),
    }, { cancelRefetch: false });
  }, [client, path]);

  // 沿用主列表实际403，不根据角色猜scope，也不额外发权限探测请求。
  // 明细403可能仅是某条记录不可见，不应关闭整个模块。
  useEffect(() => client.getQueryCache().subscribe((event) => {
    if (event.type !== "updated") return;
    const module = moduleForListQuery(event.query.queryKey);
    if (!module) return;
    if (event.action.type === "success") {
      setDenied((previous) => previous.includes(module.path)
        ? previous.filter((item) => item !== module.path) : previous);
      return;
    }
    if (event.action.type !== "error" || !isForbiddenError(event.query.state.error)) return;
    setDenied((previous) => previous.includes(module.path) ? previous : [...previous, module.path]);
    client.removeQueries({ predicate: (query) =>
      (module.queries as readonly unknown[]).includes(query.queryKey[0]) });
    if (module.path === path) navigate("/", { replace: true });
  }), [client, navigate, path]);

  useEffect(() => { activeLink.current?.scrollIntoView?.({ block: "nearest", inline: "nearest" }); }, [path, tabs.length, productionAccess]);

  const visible = tabs.filter((tab) => !warehouseOnly && !denied.includes(tab.path) &&
    (tab.path !== "/production" || productionAccess === true));

  function closeTab(closingPath: string) {
    if (path === closingPath) {
      const index = visible.findIndex((tab) => tab.path === closingPath);
      const remaining = visible.filter((tab) => tab.path !== closingPath);
      navigate(remaining[Math.max(0, index - 1)]?.href ?? "/");
    }
    setTabs((previous) => previous.filter((tab) => tab.path !== closingPath));
  }

  if (!visible.length) return null;
  return <nav className="module-tabs" aria-label="已打开模块">
    {visible.map((tab) => {
      const active = path === tab.path;
      const label = moduleFor(tab.path)!.label;
      return <div key={tab.path} className={`module-tab${active ? " module-tab-active" : ""}`}>
        <Link to={tab.href} aria-current={active ? "page" : undefined}
          ref={active ? activeLink : undefined}>{label}</Link>
        <Button type="text" size="small" icon={<CloseOutlined />} aria-label={`关闭${label}`}
          onClick={() => closeTab(tab.path)} />
      </div>;
    })}
  </nav>;
}

