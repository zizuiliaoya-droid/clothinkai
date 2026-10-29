// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Outlet, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModuleNavigation } from "./ModuleNavigation";
import { MODULES, navigationStorageKey } from "./moduleTabsStorage";
import { useAuthStore } from "@/stores/authStore";
import { queryClient } from "@/services/queryClient";
import type { UserSummary } from "@/types";

const user: UserSummary = { id: "tenant-a-user", username: "a", display_name: null, email: null, status: "active", roles: ["admin"] };
const load = vi.fn<(path: string) => Promise<string>>();
let clients: QueryClient[] = [];

beforeEach(() => {
  localStorage.clear();
  load.mockReset().mockImplementation(async (path) => `fresh:${path}`);
  useAuthStore.setState({ user, isAuthenticated: true });
});
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients = [];
  queryClient.clear();
  vi.restoreAllMocks();
  useAuthStore.setState({ user: null, isAuthenticated: false });
});

function Page({ path }: { path: string }) {
  const location = useLocation();
  const queryKey = path === "/production"
    ? ["production", "month", undefined, undefined, false, []]
    : [path.slice(1), { list: true }];
  const { data, error } = useQuery({ queryKey, queryFn: () => load(path) });
  return <><output aria-label="当前地址">{location.pathname + location.search + location.hash}</output>
    <output aria-label="页面数据">{error ? "无权限" : data}</output>
    <Link to={`${path}?goods_id=g1&season=spring&season=summer&pr_user_id=p1&id=detail-1#detail`}>更新筛选和详情</Link></>;
}

function Shell({ productionAccess }: { productionAccess?: boolean }) {
  const navigate = useNavigate();
  return <><div aria-label="菜单">{MODULES.map((module) =>
    <Link key={module.path} to={module.path}>打开{module.label}</Link>)}<Link to="/users">用户管理</Link></div>
    <button onClick={() => navigate(-1)}>后退</button><button onClick={() => navigate(1)}>前进</button>
    <ModuleNavigation productionAccess={productionAccess} /><Outlet /></>;
}

function mount({ initial = "/", productionAccess = true, client }: {
  initial?: string; productionAccess?: boolean; client?: QueryClient;
} = {}) {
  const qc = client ?? new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  clients.push(qc);
  const tree = (access: boolean | undefined) => <QueryClientProvider client={qc}>
    <MemoryRouter initialEntries={[initial]}><Routes><Route element={<Shell productionAccess={access} />}>
      <Route path="/" element={<span>概览页面</span>} />
      <Route path="/users" element={<span>用户管理页面</span>} />
      {MODULES.map((module) => <Route key={module.path} path={module.path} element={<Page key={module.path} path={module.path} />} />)}
    </Route></Routes></MemoryRouter></QueryClientProvider>;
  return { ...render(tree(productionAccess)), qc, tree };
}

function tabs() { return within(screen.getByRole("navigation", { name: "已打开模块" })); }
async function open(label: string) { fireEvent.click(screen.getByRole("link", { name: `打开${label}` })); await screen.findByRole("button", { name: `关闭${label}` }); }

describe("五模块路由页签", () => {
  it("菜单打开五模块各一个页签，切换保留完整地址，重复打开不重复，普通页面不入签", async () => {
    mount();
    for (const module of MODULES) await open(module.label);
    expect(tabs().getAllByRole("link")).toHaveLength(5);
    await open("站外推广");
    fireEvent.click(screen.getByRole("link", { name: "更新筛选和详情" }));
    const saved = "/promotions?goods_id=g1&season=spring&season=summer&pr_user_id=p1&id=detail-1#detail";
    await waitFor(() => expect(tabs().getByRole("link", { name: "站外推广" }).getAttribute("href")).toBe(saved));
    fireEvent.click(tabs().getByRole("link", { name: "博主库" }));
    fireEvent.click(tabs().getByRole("link", { name: "站外推广" }));
    expect(screen.getByLabelText("当前地址").textContent).toBe(saved);
    expect(tabs().getAllByRole("link")).toHaveLength(5);
    fireEvent.click(screen.getByRole("link", { name: "用户管理" }));
    expect(screen.getByText("用户管理页面")).toBeTruthy();
    expect(tabs().getAllByRole("link")).toHaveLength(5);
    expect(tabs().getAllByRole("link").every((link) => !link.hasAttribute("aria-current"))).toBe(true);
  });

  it("关闭非当前不跳转，关闭当前选相邻，关闭最后回概览；刷新恢复剩余页签", async () => {
    const first = mount();
    await open("博主库"); await open("谈款审核"); await open("站外推广");
    fireEvent.click(tabs().getByRole("button", { name: "关闭谈款审核" }));
    expect(screen.getByLabelText("当前地址").textContent).toBe("/promotions");
    first.unmount();
    mount({ initial: "/promotions" });
    expect(tabs().getAllByRole("link")).toHaveLength(2);
    fireEvent.click(tabs().getByRole("button", { name: "关闭站外推广" }));
    expect(screen.getByLabelText("当前地址").textContent).toBe("/bloggers");
    fireEvent.click(tabs().getByRole("button", { name: "关闭博主库" }));
    expect(screen.getByText("概览页面")).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("深链、查询串和浏览器前进后退保持路由语义；后退可重新打开已关闭模块", async () => {
    mount({ initial: "/promotions?id=p9&goods_id=g9" });
    await open("催发任务");
    fireEvent.click(screen.getByText("后退"));
    expect(screen.getByLabelText("当前地址").textContent).toBe("/promotions?id=p9&goods_id=g9");
    fireEvent.click(screen.getByText("前进"));
    expect(screen.getByLabelText("当前地址").textContent).toBe("/urge-tasks");
    fireEvent.click(tabs().getByRole("button", { name: "关闭催发任务" }));
    fireEvent.click(screen.getByText("后退"));
    await screen.findByRole("button", { name: "关闭催发任务" });
    expect(screen.getByLabelText("当前地址").textContent).toBe("/urge-tasks");
  });

  it("切回模块使仍fresh的缓存重新取数，未保活页面", async () => {
    mount();
    await open("博主库");
    await waitFor(() => expect(screen.getByLabelText("页面数据").textContent).toBe("fresh:/bloggers"));
    await open("谈款审核");
    load.mockImplementation(async (path) => `updated:${path}`);
    fireEvent.click(tabs().getByRole("link", { name: "博主库" }));
    await waitFor(() => expect(screen.getByLabelText("页面数据").textContent).toBe("updated:/bloggers"));
    expect(load.mock.calls.filter(([path]) => path === "/bloggers")).toHaveLength(2);
  });

  it("账号隔离、角色变化清旧页签；能力撤销只裁掉投产，加载中不抹掉其偏好", async () => {
    const view = mount();
    await open("博主库"); await open("投产报表");
    fireEvent.click(screen.getByRole("link", { name: "用户管理" }));
    view.rerender(view.tree(undefined));
    expect(tabs().queryByRole("link", { name: "投产报表" })).toBeNull();
    view.rerender(view.tree(true));
    expect(tabs().getByRole("link", { name: "投产报表" })).toBeTruthy();
    view.rerender(view.tree(false));
    expect(tabs().queryByRole("link", { name: "投产报表" })).toBeNull();
    act(() => useAuthStore.setState({ user: { ...user, id: "tenant-b-user" } }));
    expect(screen.queryByRole("navigation")).toBeNull();
    await open("谈款审核");
    expect(tabs().queryByRole("link", { name: "博主库" })).toBeNull();
    fireEvent.click(screen.getByRole("link", { name: "用户管理" }));
    act(() => useAuthStore.setState({ user: { ...user, roles: ["operations"] } }));
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("只有模块主列表403关闭失效页签；详情或子请求403不误关", async () => {
    const { qc } = mount();
    await open("站外推广");
    const forbidden = { response: { status: 403 } };
    await act(async () => { await qc.fetchQuery({ queryKey: ["promotions", "detail", "foreign"], queryFn: async () => { throw forbidden; } }).catch(() => {}); });
    expect(tabs().getByRole("link", { name: "站外推广" })).toBeTruthy();
    await act(async () => { await qc.fetchQuery({ queryKey: ["promotion-attachments", "p"], queryFn: async () => { throw forbidden; } }).catch(() => {}); });
    expect(tabs().getByRole("link", { name: "站外推广" })).toBeTruthy();
    load.mockRejectedValue(forbidden);
    await open("博主库");
    await screen.findByText("概览页面");
    expect(tabs().queryByRole("link", { name: "博主库" })).toBeNull();
    expect(tabs().getByRole("link", { name: "站外推广" })).toBeTruthy();
    load.mockResolvedValue("restored permission");
    await open("博主库");
    expect(tabs().getByRole("link", { name: "博主库" })).toBeTruthy();
  });

  it.each([
    ["站外推广", "/promotions", ["promotions", "filter-options", "", 1, undefined]],
    ["博主库", "/bloggers", ["bloggers", "picker-options", "小红书"]],
  ] as const)("%s辅助选项403不关签、不清主列表缓存或跳转", async (label, path, key) => {
    const { qc } = mount();
    await open(label);
    await waitFor(() => expect(screen.getByLabelText("页面数据").textContent).toBe(`fresh:${path}`));
    await act(async () => {
      await qc.fetchQuery({ queryKey: key, queryFn: async () => { throw { response: { status: 403 } }; } }).catch(() => {});
    });
    expect(tabs().getByRole("link", { name: label })).toBeTruthy();
    expect(screen.getByLabelText("当前地址").textContent).toBe(path);
    expect(qc.getQueryData([path.slice(1), { list: true }])).toBe(`fresh:${path}`);
  });

  it("五模块仅真实主列表成功恢复失权页签，辅助选项成功不恢复", async () => {
    const { qc } = mount();
    for (const module of MODULES) {
      load.mockRejectedValue({ response: { status: 403 } });
      fireEvent.click(screen.getByRole("link", { name: `打开${module.label}` }));
      await screen.findByText("概览页面");
      expect(screen.queryByRole("button", { name: `关闭${module.label}` })).toBeNull();
      let resolve!: (value: string) => void;
      load.mockImplementation(() => new Promise<string>((done) => { resolve = done; }));
      fireEvent.click(screen.getByRole("link", { name: `打开${module.label}` }));
      await act(async () => {
        await qc.fetchQuery({ queryKey: [module.path.slice(1), "picker-options", "*"], queryFn: async () => ["option"] });
      });
      expect(screen.queryByRole("button", { name: `关闭${module.label}` })).toBeNull();
      await act(async () => resolve("permission restored"));
      await screen.findByRole("button", { name: `关闭${module.label}` });
    }
  });

  it("存储损坏/禁用不阻塞导航；保存地址不接受外站或非模块路由", async () => {
    localStorage.setItem(navigationStorageKey(user.id), JSON.stringify({ roles: JSON.stringify(user.roles), tabs: [
      { path: "/bloggers", href: "https://outside.example/bloggers" }, { path: "/users", href: "/users" },
      { path: "/promotions", href: "/promotions?id=safe" },
    ] }));
    mount();
    expect(tabs().getAllByRole("link")).toHaveLength(1);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    await open("博主库");
    expect(tabs().getAllByRole("link")).toHaveLength(2);
  });
});

describe("认证边界清理业务缓存", () => {
  it("同用户资料更新不清理，身份/角色变化和退出清理；旧未完成请求不回填下一账号", async () => {
    queryClient.setQueryData(["promotions"], "user-a-data");
    useAuthStore.getState().setUser({ ...user, display_name: "new name" });
    expect(queryClient.getQueryData(["promotions"])).toBe("user-a-data");
    let resolve!: (value: string) => void;
    const old = queryClient.fetchQuery({ queryKey: ["old-pending"], queryFn: () => new Promise<string>((done) => { resolve = done; }) }).catch(() => undefined);
    useAuthStore.getState().setSession({ ...user, id: "tenant-b-user" }, "synthetic-access", "synthetic-refresh", false);
    resolve("old-private-data"); await old;
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    queryClient.setQueryData(["production"], "data");
    useAuthStore.getState().setUser({ ...user, id: "tenant-b-user", roles: ["finance"] });
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    queryClient.setQueryData(["bloggers"], "data");
    useAuthStore.getState().logout();
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
  });
});

