// @vitest-environment jsdom
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Outlet } from "react-router-dom";
import { useAuthStore } from "@/stores/authStore";
import { useSupportStore } from "@/stores/supportStore";
import { getMe } from "@/features/auth/api";
import App from "./App";

vi.mock("@/features/auth/api", () => ({ getMe: vi.fn(), logout: vi.fn() }));
vi.mock("@/components/AppLayout/AppLayout", () => ({ AppLayout: () => <><div>客户布局</div><Outlet /></> }));
vi.mock("@/components/AppLayout/PlatformLayout", () => ({ PlatformLayout: () => <><div>平台布局</div><Outlet /></> }));
vi.mock("@/pages/TenantListPage", () => ({ TenantListPage: () => <div>租户控制台</div> }));
vi.mock("@/pages/GoodsPage", () => ({ GoodsPage: () => <div>客户商品只读页</div> }));
vi.mock("@/pages/HomePage", () => ({ HomePage: () => <div>客户首页</div> }));
vi.mock("@/pages/LoginPage", () => ({ LoginPage: () => <div>登录页</div> }));
vi.mock("@/pages/ChangePasswordPage", () => ({ ChangePasswordPage: () => <div>改密页</div> }));
const platform = { id: "owner", username: "platform_owner", display_name: null, email: null, status: "active", roles: ["platform_admin"], session_mode: "platform" as const };
const grant = { access_token: "support", expires_in: 900, tenant: { id: "a", code: "a", name: "客户 A" } };
beforeEach(() => {
  useSupportStore.getState().end();
  useAuthStore.setState({ user: platform, isAuthenticated: true, mustChangePassword: false });
  vi.mocked(getMe).mockResolvedValue(platform);
});
afterEach(() => { cleanup(); useSupportStore.getState().end(); vi.useRealTimers(); });
function open(path: string) { window.history.replaceState({}, "", path); render(<App />); }

describe("平台与客户路由分离", () => {
  it("平台账号直接输入客户页面也回独立平台控制台", async () => {
    open("/promotions");
    expect(await screen.findByText("租户控制台")).toBeTruthy();
    expect(window.location.pathname).toBe("/platform");
    expect(screen.queryByText("客户布局")).toBeNull();
  });
  it("客户管理员不能进入平台控制台", async () => {
    const customer = { ...platform, roles: ["admin"], session_mode: "customer" as const };
    useAuthStore.setState({ user: customer }); vi.mocked(getMe).mockResolvedValue(customer);
    open("/platform");
    expect(await screen.findByText("客户首页")).toBeTruthy();
    expect(screen.queryByText("租户控制台")).toBeNull();
  });
  it("支持会话不能进入凭据等未开放页面，返回平台即清理内存令牌", async () => {
    useSupportStore.getState().start(grant);
    open("/credentials");
    expect(await screen.findByText("客户商品只读页")).toBeTruthy();
    expect(window.location.pathname).toBe("/goods");
    act(() => { window.history.pushState({}, "", "/platform"); window.dispatchEvent(new PopStateEvent("popstate")); });
    expect(await screen.findByText("租户控制台")).toBeTruthy();
    expect(useSupportStore.getState().session).toBeNull();
    expect(useAuthStore.getState().user?.id).toBe("owner");
  });
  it("支持到期自动返回平台，仍保留平台登录", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    useSupportStore.getState().start({ ...grant, expires_in: 1 });
    open("/goods");
    await waitFor(() => expect(screen.getByText("客户商品只读页")).toBeTruthy());
    await act(async () => { vi.advanceTimersByTime(1100); });
    expect(await screen.findByText("租户控制台")).toBeTruthy();
    expect(useAuthStore.getState().isAuthenticated).toBe(true);
  });
  it("只读模式不持久化：刷新后的平台身份不会停留客户页", async () => {
    open("/goods");
    expect(await screen.findByText("租户控制台")).toBeTruthy();
    expect(useSupportStore.getState().session).toBeNull();
  });
});
