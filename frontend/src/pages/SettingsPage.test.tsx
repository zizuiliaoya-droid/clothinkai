// @vitest-environment jsdom
// 系统设置页的 Tab 显隐（7e R7）：由后端有效读权限决定，个人授权 / 撤回不能靠角色推断。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const getPlatformMock = vi.fn();
vi.mock("@/features/settings/api", () => ({
  getWecomConfig: vi.fn().mockResolvedValue(null),
  updateWecomConfig: vi.fn(),
  testWecom: vi.fn(),
  getPromotionPlatformSettings: (...args: unknown[]) => getPlatformMock(...args),
  updatePromotionPlatformSettings: vi.fn(),
}));
vi.mock("@/features/security/components/NetworkDiagnosticsPanel", () => ({
  NetworkDiagnosticsPanel: () => <div>网络诊断内容</div>,
}));

import { SettingsPage } from "./SettingsPage";
import { useAuthStore } from "@/stores/authStore";

beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (elt: Element) => getStyle(elt);
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }
});

function asRole(role: string) {
  useAuthStore.setState({
    user: {
      id: `u-${role}`,
      username: role,
      display_name: null,
      email: null,
      status: "active",
      roles: [role],
    },
    isAuthenticated: true,
  });
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return { ...render(<QueryClientProvider client={client}>{node}</QueryClientProvider>), client };
}

beforeEach(() => {
  getPlatformMock.mockReset();
  getPlatformMock.mockResolvedValue({
    items: [{ platform: "抖音", like_coefficient: "0.100", hit_threshold: 10000 }],
    can_edit: false,
    changed: [],
    quality_recompute_queued: false,
  });
});

afterEach(cleanup);

describe("SettingsPage", () => {
  it.each(["admin", "platform_admin", "pr_manager", "operations"])(
    "%s 的读权限探测成功后有「平台指标」Tab，点开复用数据",
    async (role) => {
      asRole(role);
      wrap(<SettingsPage />);
      const tab = await screen.findByRole("tab", { name: "平台指标" });
      expect(getPlatformMock).toHaveBeenCalledTimes(1);
      fireEvent.click(tab);
      expect(await screen.findByText("10,000")).toBeTruthy();
      expect(getPlatformMock).toHaveBeenCalledTimes(1);
    }
  );

  it.each(["pr", "finance", "pr_manager"])("%s 没有有效读权限：隐藏 Tab，没有 403 错误框", async (role) => {
    getPlatformMock.mockRejectedValue({
      response: { status: 403, data: { code: "PERMISSION_DENIED", message: "缺少权限 metric_config:read" } },
    });
    asRole(role);
    const { client } = wrap(<SettingsPage />);
    expect(await screen.findByRole("tab", { name: "企业微信" })).toBeTruthy();
    await waitFor(() => expect(client.getQueryState(["promotion-platform-settings", `u-${role}`])?.status).toBe("error"));
    expect(getPlatformMock).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("tab", { name: "平台指标" })).toBeNull();
    expect(screen.queryByText("缺少权限 metric_config:read")).toBeNull();
    expect(document.querySelector(".ant-alert-error")).toBeNull();
  });

  it("非预设角色个人获得读权限，也有 Tab；编辑仍由 can_edit 决定", async () => {
    asRole("designer");
    wrap(<SettingsPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "平台指标" }));
    expect(await screen.findByText("10,000")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "保存修改" })).toBeNull();
    expect(screen.queryAllByRole("spinbutton")).toHaveLength(0);
  });

  it("PR 个人获得读写权限，can_edit 为真时可以编辑", async () => {
    getPlatformMock.mockResolvedValue({
      items: [{ platform: "抖音", like_coefficient: "0.100", hit_threshold: 10000 }],
      can_edit: true,
      changed: [],
      quality_recompute_queued: false,
    });
    asRole("pr");
    wrap(<SettingsPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "平台指标" }));
    expect(await screen.findByLabelText("抖音点赞折算系数")).toBeTruthy();
    expect(screen.getByRole("button", { name: "保存修改" })).toBeTruthy();
  });

  it("探测未返回时不凭角色先显示 Tab", async () => {
    let resolve!: (value: unknown) => void;
    getPlatformMock.mockReturnValue(new Promise((done) => { resolve = done; }));
    asRole("pr_manager");
    wrap(<SettingsPage />);
    await waitFor(() => expect(getPlatformMock).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("tab", { name: "平台指标" })).toBeNull();
    await act(async () => resolve({ items: [], can_edit: false, changed: [], quality_recompute_queued: false }));
    expect(await screen.findByRole("tab", { name: "平台指标" })).toBeTruthy();
  });

  it("切换用户重新探测，不复用上一用户的授权缓存", async () => {
    asRole("designer");
    wrap(<SettingsPage />);
    expect(await screen.findByRole("tab", { name: "平台指标" })).toBeTruthy();
    getPlatformMock.mockRejectedValue({
      response: { status: 403, data: { code: "PERMISSION_DENIED", message: "缺少权限 metric_config:read" } },
    });
    act(() => asRole("pr_manager"));
    await waitFor(() => expect(getPlatformMock).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("tab", { name: "平台指标" })).toBeNull();
    expect(screen.queryByText("缺少权限 metric_config:read")).toBeNull();
  });

  it("已打开的 Tab 在读权限撤回后隐藏，旧数据缓存不能继续授权", async () => {
    asRole("pr_manager");
    const { client } = wrap(<SettingsPage />);
    fireEvent.click(await screen.findByRole("tab", { name: "平台指标" }));
    expect(await screen.findByText("10,000")).toBeTruthy();
    getPlatformMock.mockRejectedValue({
      response: { status: 403, data: { code: "PERMISSION_DENIED", message: "缺少权限 metric_config:read" } },
    });
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["promotion-platform-settings", "u-pr_manager"] });
    });
    await waitFor(() => expect(screen.queryByRole("tab", { name: "平台指标" })).toBeNull());
    expect(screen.queryByText("缺少权限 metric_config:read")).toBeNull();
    expect(document.querySelector(".ant-alert-error")).toBeNull();
  });
});
