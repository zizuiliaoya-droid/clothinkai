// @vitest-environment jsdom
// 系统设置页的 Tab 显隐（7e R7）：「平台指标」只给管理员 / 平台管理员 / PR 主管 / 运营；PR、财务看不到。
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
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
    "%s 看得到「平台指标」Tab，点开才取数",
    async (role) => {
      asRole(role);
      wrap(<SettingsPage />);
      const tab = await screen.findByRole("tab", { name: "平台指标" });
      expect(getPlatformMock).not.toHaveBeenCalled();
      fireEvent.click(tab);
      expect(await screen.findByText("10,000")).toBeTruthy();
      expect(getPlatformMock).toHaveBeenCalledTimes(1);
    }
  );

  it.each(["pr", "finance"])("%s 看不到「平台指标」Tab，也不取数", async (role) => {
    asRole(role);
    wrap(<SettingsPage />);
    expect(await screen.findByRole("tab", { name: "企业微信" })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: "平台指标" })).toBeNull();
    expect(getPlatformMock).not.toHaveBeenCalled();
  });
});
