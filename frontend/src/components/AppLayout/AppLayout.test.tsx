// @vitest-environment jsdom
// 左侧菜单：
// - 「仓库发货」只对仓库 / 管理员显示（PR-2 起仓库页只走 promotion_ship:fill / export，其余角色点进去是 403）；
// - 「工作进度表」「BI看板」按 GET /api/reports/capabilities 显示（7e R1）：能力没回来前都不显示。
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/features/auth/api", () => ({ logout: vi.fn() }));
vi.mock("@/features/report/api", () => ({ getReportCapabilities: vi.fn() }));

import { AppLayout } from "@/components/AppLayout/AppLayout";
import { getReportCapabilities } from "@/features/report/api";
import type { ReportCapabilities } from "@/features/report/types";
import { useAuthStore } from "@/stores/authStore";

const NO_CAPS: ReportCapabilities = {
  work_progress: null,
  publish_progress: null,
  bi: null,
  production: false,
  production_margin: false,
  store_read: false,
  store_write: false,
  export: [],
  refresh: false,
};

beforeAll(() => {
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

beforeEach(() => {
  vi.mocked(getReportCapabilities).mockReset();
  vi.mocked(getReportCapabilities).mockResolvedValue(NO_CAPS);
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null, isAuthenticated: false });
});

function renderLayout() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/"]}>
        <AppLayout />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

function renderAs(roles: string[]) {
  useAuthStore.setState({
    user: { id: "u1", username: "u1", display_name: null, email: null, status: "active", roles },
    isAuthenticated: true,
  });
  return renderLayout();
}

describe("AppLayout 菜单「仓库发货」", () => {
  it.each([["pr"], ["pr_manager"], ["finance"], ["operations"]])(
    "%s 没有发货权限：不显示「仓库发货」",
    (role) => {
      renderAs([role]);
      // 场景有效性：推广管理分组是展开的，其他菜单项在
      expect(screen.getByText("站外推广")).toBeTruthy();
      expect(screen.getByText("催发任务")).toBeTruthy();
      expect(screen.queryByText("仓库发货")).toBeNull();
    }
  );

  it.each([["admin"], ["platform_admin"]])("%s 显示「仓库发货」", (role) => {
    renderAs([role]);
    expect(screen.getByText("站外推广")).toBeTruthy();
    expect(screen.getByText("仓库发货")).toBeTruthy();
  });

  it("仓库角色照旧只有「仓库发货」一项", () => {
    renderAs(["warehouse"]);
    expect(screen.getByText("仓库发货")).toBeTruthy();
    expect(screen.queryByText("站外推广")).toBeNull();
  });
});

describe("AppLayout 报表菜单按 capabilities", () => {
  // 能力组合取自后端 build_capabilities 对真实角色的结果（S8 期望表）；菜单只看能力、不看角色
  it.each<[string, Partial<ReportCapabilities>, boolean, boolean]>([
    ["pr（只看本人、BI 个人版）", { work_progress: "own", publish_progress: "own", bi: "personal" }, true, false],
    ["finance（工作进度全部、BI 全量）", { work_progress: "all", bi: "full", export: ["work-progress"] }, true, true],
    ["pr_manager（全部）", { work_progress: "all", publish_progress: "all", bi: "full" }, true, true],
    ["只有 BI 全量", { bi: "full" }, false, true],
  ])("%s", async (_name, caps, showWorkProgress, showBi) => {
    vi.mocked(getReportCapabilities).mockResolvedValue({ ...NO_CAPS, ...caps });
    renderAs(["pr"]);
    // 等能力回来：应出现的那一项作锚点
    await screen.findByText(showWorkProgress ? "工作进度表" : "BI看板");
    expect(screen.queryByText("工作进度表") != null).toBe(showWorkProgress);
    expect(screen.queryByText("BI看板") != null).toBe(showBi);
    // 场景有效性：同组的其他菜单照常
    expect(screen.getByText("发文进度表")).toBeTruthy();
    expect(screen.getByText("投产报表")).toBeTruthy();
  });

  it("没有报表能力：两项都不显示（能力已回来）", async () => {
    renderAs(["pr"]);
    await vi.waitFor(() => expect(getReportCapabilities).toHaveBeenCalledTimes(1));
    // 让查询结果落定后再断言「不在」，不只测到加载中
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText("站外推广")).toBeTruthy();
    expect(screen.queryByText("工作进度表")).toBeNull();
    expect(screen.queryByText("BI看板")).toBeNull();
  });

  it("能力没回来前不显示（不先出现再收回）", () => {
    vi.mocked(getReportCapabilities).mockReturnValue(new Promise(() => {}));
    renderAs(["admin"]);
    expect(screen.getByText("站外推广")).toBeTruthy();
    expect(screen.queryByText("工作进度表")).toBeNull();
    expect(screen.queryByText("BI看板")).toBeNull();
  });

  it("未登录不查能力", () => {
    renderLayout();
    expect(getReportCapabilities).not.toHaveBeenCalled();
  });
});