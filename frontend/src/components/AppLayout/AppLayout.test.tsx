// @vitest-environment jsdom
import { useSupportStore } from "@/stores/supportStore";
// 左侧菜单：
// - 「仓库发货」只对仓库 / 管理员显示（PR-2 起仓库页只走 promotion_ship:fill / export，其余角色点进去是 403）；
// - 报表各项按 GET /api/reports/capabilities 显示（7e R1 / R4）：能力没回来前都不显示；
//   单品投产与店铺日 / 周 / 月归「投产分析」分组（空组不出）。
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
  // 能力组合取自后端 build_capabilities 对真实角色的结果（S8 期望表）；菜单只看能力、不看角色。
  // 7e R4：发文进度表、单品投产、店铺日 / 周 / 月也按能力显隐；后四项归「投产分析」分组，空组不出
  const REPORT_ITEMS = [
    "工作进度表",
    "发文进度表",
    "投产分析",
    "单品投产",
    "店铺日明细",
    "店铺周汇总",
    "店铺月度汇总",
    "BI看板",
  ] as const;

  it.each<[string, Partial<ReportCapabilities>, string[]]>([
    [
      "finance（工作进度全部、BI 全量；无店铺 / 投产 / 发文进度读权限）",
      { work_progress: "all", bi: "full", export: ["work-progress"] },
      ["工作进度表", "BI看板"],
    ],
    [
      "pr_manager（全部）",
      { work_progress: "all", publish_progress: "all", bi: "full", production: true, store_read: true },
      [...REPORT_ITEMS],
    ],
    [
      "pr（只看本人、BI 个人版；7e R6 起个人版也进菜单）",
      { work_progress: "own", publish_progress: "own", bi: "personal" },
      ["工作进度表", "发文进度表", "BI看板"],
    ],
    ["只有 BI 个人版", { bi: "personal" }, ["BI看板"]],
    ["只有 store_read", { store_read: true }, ["投产分析", "店铺日明细", "店铺周汇总", "店铺月度汇总"]],
    ["只有单品投产", { production: true }, ["投产分析", "单品投产"]],
    ["只有 BI 全量", { bi: "full" }, ["BI看板"]],
  ])("%s", async (_name, caps, visible) => {
    vi.mocked(getReportCapabilities).mockResolvedValue({ ...NO_CAPS, ...caps });
    renderAs(["pr"]);
    // 等能力回来：应出现的第一项作锚点
    await screen.findByText(visible[0]);
    for (const item of REPORT_ITEMS) {
      expect(screen.queryByText(item) != null, item).toBe(visible.includes(item));
    }
    // 投产分析下的四项都没有、BI 也没有时，「报表与分析」整组不出
    const anyReport = visible.some((v) => !["工作进度表", "发文进度表"].includes(v));
    expect(screen.queryByText("报表与分析") != null).toBe(anyReport);
    // 场景有效性：其他菜单照常；爆款约篇不看能力
    expect(screen.getByText("站外推广")).toBeTruthy();
    expect(screen.getByText("爆款约篇数量")).toBeTruthy();
  });

  it("店铺三项指向新路由", async () => {
    vi.mocked(getReportCapabilities).mockResolvedValue({ ...NO_CAPS, production: true, store_read: true });
    renderAs(["pr"]);
    const daily = await screen.findByRole("link", { name: "店铺日明细" });
    expect(daily.getAttribute("href")).toBe("/store/daily");
    expect(screen.getByRole("link", { name: "店铺周汇总" }).getAttribute("href")).toBe("/store/weekly");
    expect(screen.getByRole("link", { name: "店铺月度汇总" }).getAttribute("href")).toBe("/store/monthly");
    expect(screen.getByRole("link", { name: "单品投产" }).getAttribute("href")).toBe("/production");
  });

  it("没有报表能力：各项都不显示（能力已回来）", async () => {
    renderAs(["pr"]);
    await vi.waitFor(() => expect(getReportCapabilities).toHaveBeenCalledTimes(1));
    // 让查询结果落定后再断言「不在」，不只测到加载中
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText("站外推广")).toBeTruthy();
    for (const item of REPORT_ITEMS) expect(screen.queryByText(item), item).toBeNull();
    expect(screen.queryByText("报表与分析")).toBeNull();
  });

  it("能力没回来前不显示（不先出现再收回）", () => {
    vi.mocked(getReportCapabilities).mockReturnValue(new Promise(() => {}));
    renderAs(["admin"]);
    expect(screen.getByText("站外推广")).toBeTruthy();
    for (const item of REPORT_ITEMS) expect(screen.queryByText(item), item).toBeNull();
    expect(screen.queryByText("报表与分析")).toBeNull();
  });

  it("未登录不查能力", () => {
    renderLayout();
    expect(getReportCapabilities).not.toHaveBeenCalled();
  });
});

it("只读客户布局持续标明客户和退出入口，菜单不含管理/凭据/导入", () => {
  useSupportStore.getState().start({ access_token: "support", expires_in: 900, tenant: { id: "a", code: "client-a", name: "客户 A" } });
  renderAs(["platform_admin"]);
  expect(screen.getByText(/客户 A.*client-a/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "返回平台" })).toBeTruthy();
  expect(screen.queryByText("采集凭据")).toBeNull();
  expect(screen.queryByText("系统设置")).toBeNull();
  expect(screen.queryByText("仓库发货")).toBeNull();
  expect(screen.getByText("商品 / 款式")).toBeTruthy();
});
