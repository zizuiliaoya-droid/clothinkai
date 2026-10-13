// @vitest-environment jsdom
// 工作进度页（7e R5 S7）：季节锁定 = 筛选记忆（清空写 []）、URL 季节只本次生效；PR 表新列与「推广成本」改名；
// 导出与下钻带季节；403 显示无权限提示；只看本人没有导出按钮。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { PrWorkProgress, ReportCapabilities } from "@/features/report/types";

const workProgressMock = vi.fn();
const exportMock = vi.fn();
const drillMock = vi.fn();
const getPrefMock = vi.fn();
const savePrefMock = vi.fn();
const caps = vi.hoisted(() => ({
  workProgress: "all" as "all" | "own" | null,
  export: ["work-progress"] as string[],
}));
vi.mock("@/features/report/api", () => ({
  getWorkProgress: (...args: unknown[]) => workProgressMock(...args),
  exportReport: (...args: unknown[]) => exportMock(...args),
  getDrillPromotions: (...args: unknown[]) => drillMock(...args),
  getReportSeasonOptions: () => Promise.resolve(["2026秋", "2026冬"]),
  getReportCapabilities: () =>
    Promise.resolve({
      work_progress: caps.workProgress,
      publish_progress: null,
      bi: null,
      production: false,
      production_margin: false,
      store_read: false,
      store_write: false,
      export: caps.export,
      refresh: false,
    } satisfies ReportCapabilities),
  getSummaryFreshness: () =>
    Promise.resolve({
      date_from: "2026-10-01",
      date_to: "2026-10-31",
      source: "live",
      data_as_of: null,
      can_refresh: false,
    }),
  refreshSummaries: () => Promise.resolve(undefined),
}));
vi.mock("@/features/preference/api", () => ({
  getFilterPreference: (...args: unknown[]) => getPrefMock(...args),
  saveFilterPreference: (...args: unknown[]) => savePrefMock(...args),
}));

import { useAuthStore } from "@/stores/authStore";
import { WorkProgressPage } from "./WorkProgressPage";

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

const PR1 = "11111111-1111-4111-8111-111111111111";

function prRow(over: Partial<PrWorkProgress> = {}): PrWorkProgress {
  return {
    pr_id: PR1,
    pr_name: "小王",
    quote_count: 10,
    effective_quote_count: 8,
    in_schedule_count: 2,
    urge_count: 1,
    important_urge_count: 0,
    overdue_count: 1,
    publish_count: 4,
    info_complete_count: 3,
    info_complete_rate: "0.7500",
    cancel_count: 1,
    recall_due_count: 1,
    recall_success_count: 1,
    recall_complete_rate: "1.0000",
    overdue_rate: "0.1250",
    month_complete_rate: "0.5000",
    hit_count: 1,
    hit_rate: "0.2500",
    like_count: 300,
    cost: "88.00",
    cpl: "1.40",
    quote_amount: "1000.00",
    published_quote_amount: "400.00",
    effective_quote_amount: "800.00",
    unpublished_quote_amount: "500.00",
    cancelled_quote_amount: "100.00",
    promo_cost: "420.00",
    unpublished_count: 5,
    shipped_count: 5,
    shipped_published_count: 4,
    avg_published_quote: "100.00",
    amount_publish_rate: "0.4000",
    total_amount_publish_rate: "0.5000",
    ship_publish_rate: "0.8000",
    ...over,
  };
}

// 块体写法：mockReset() 返回 mock 本身，简写箭头会被 vitest 当成清理函数
beforeEach(() => {
  workProgressMock.mockReset();
  workProgressMock.mockResolvedValue([prRow()]);
  exportMock.mockReset();
  exportMock.mockResolvedValue("work-progress.xlsx");
  drillMock.mockReset();
  drillMock.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 });
  getPrefMock.mockReset();
  getPrefMock.mockResolvedValue({});
  savePrefMock.mockReset();
  savePrefMock.mockResolvedValue(undefined);
  caps.workProgress = "all";
  caps.export = ["work-progress"];
  // 能力查询要登录才发
  useAuthStore.setState({
    user: { id: "u1", username: "u1", display_name: null, email: null, status: "active", roles: [] },
    isAuthenticated: true,
  });
});
afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null, isAuthenticated: false });
});

function renderPage(url = "/work-progress") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <WorkProgressPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** 等足保存节流（600ms）再看有没有写记忆。 */
const waitDebounce = () => new Promise((resolve) => setTimeout(resolve, 800));

/** 打开季节下拉并点一个选项（已选的再点 = 取消）。 */
async function toggleSeason(label: string) {
  const input = screen.getByRole("combobox", { name: "季节或系列" });
  const selector = input.closest(".ant-select")?.querySelector(".ant-select-selector");
  fireEvent.mouseDown(selector as Element);
  const option = await waitFor(() => {
    const el = document.querySelector(`.ant-select-item-option[title="${label}"]`);
    expect(el).toBeTruthy();
    return el as HTMLElement;
  });
  fireEvent.click(option);
}

const LOCK_HINT = "季节已锁定，下次打开保持";

describe("WorkProgressPage 季节锁定", () => {
  it("选季节：请求带 season、写记忆、显示锁定提示；取消到空：写 [] 解锁", async () => {
    renderPage();
    await screen.findByText("小王");
    expect(workProgressMock.mock.calls[0][0].season).toBeUndefined();

    await toggleSeason("2026秋");
    await waitFor(() =>
      expect(workProgressMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ season: ["2026秋"] })
      )
    );
    expect(screen.getByText(LOCK_HINT)).toBeTruthy();
    await waitFor(() => expect(savePrefMock).toHaveBeenCalled(), { timeout: 2000 });
    expect(savePrefMock).toHaveBeenLastCalledWith(
      "pr_work_progress",
      expect.objectContaining({ season: ["2026秋"] })
    );

    await toggleSeason("2026秋");
    await waitFor(() => expect(screen.queryByText(LOCK_HINT)).toBeNull());
    await waitFor(
      () =>
        expect(savePrefMock).toHaveBeenLastCalledWith(
          "pr_work_progress",
          expect.objectContaining({ season: [] })
        ),
      { timeout: 2000 }
    );
    expect(workProgressMock.mock.calls.at(-1)?.[0].season).toBeUndefined();
  });

  it("记忆里的季节回填即生效，并显示锁定提示", async () => {
    getPrefMock.mockResolvedValue({ preset: "this_month", season: ["2026冬"] });
    renderPage();
    await screen.findByText("小王");
    expect(workProgressMock.mock.calls[0][0]).toMatchObject({ season: ["2026冬"] });
    expect(screen.getByText(LOCK_HINT)).toBeTruthy();
  });

  it("URL ?season= 只本次生效：请求带它，不写记忆、不显示锁定", async () => {
    getPrefMock.mockResolvedValue({ season: ["记忆季"] });
    renderPage("/work-progress?season=URL季");
    await screen.findByText("小王");
    expect(workProgressMock.mock.calls[0][0]).toMatchObject({ season: ["URL季"] });
    await waitDebounce();
    expect(savePrefMock).not.toHaveBeenCalled();
    expect(screen.queryByText(LOCK_HINT)).toBeNull();
  });
});

describe("WorkProgressPage PR 表", () => {
  it("列头有「推广成本」与新列，没有「成本(含衣服)」；金额与比率按格式显示", async () => {
    const { container } = renderPage();
    await screen.findByText("小王");
    const head = container.querySelector(".ant-table-thead")?.textContent ?? "";
    for (const title of [
      "推广成本",
      "约稿服务费",
      "已发布服务费",
      "发布单篇均价",
      "金额发布率",
      "总金额发布率",
      "已发货",
      "发货发布率",
      "CPL(元/赞)",
    ]) {
      expect(head).toContain(title);
    }
    expect(head).not.toContain("成本(含衣服)");
    const body = container.querySelector(".ant-table-tbody") as HTMLElement;
    expect(body.textContent).toContain("¥420.00");
    expect(body.textContent).toContain("80.0%");
    expect(body.textContent).not.toContain("¥88.00");
  });

  it("点 PR 名下钻：带该 PR、区间与当前季节", async () => {
    getPrefMock.mockResolvedValue({ season: ["2026秋"] });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "查看 小王 的推广单" }));
    await waitFor(() => expect(drillMock).toHaveBeenCalled());
    expect(drillMock.mock.calls[0][0]).toEqual({
      date_from: "2026-10-01",
      date_to: "2026-10-31",
      pr_id: PR1,
      season: ["2026秋"],
      page: 1,
      page_size: 20,
    });
    expect(await screen.findByText("小王 的推广单")).toBeTruthy();
  });
});

describe("WorkProgressPage 导出与权限", () => {
  it("导出带当前季节", async () => {
    getPrefMock.mockResolvedValue({ season: ["2026秋"] });
    renderPage();
    await screen.findByText("小王");
    fireEvent.click(await screen.findByRole("button", { name: /导出/ }));
    await waitFor(() => expect(exportMock).toHaveBeenCalled());
    expect(exportMock.mock.calls[0][0]).toBe("work-progress");
    expect(exportMock.mock.calls[0][1]).toMatchObject({ season: ["2026秋"] });
  });

  it("接口 403：显示「没有权限查看工作进度表」，不出表格", async () => {
    workProgressMock.mockRejectedValue(
      Object.assign(new Error("403"), { response: { status: 403, data: { code: "PERMISSION_DENIED" } } })
    );
    const { container } = renderPage();
    expect(await screen.findByText("没有权限查看工作进度表")).toBeTruthy();
    expect(container.querySelector(".ant-table")).toBeNull();
    // 403 不重试
    expect(workProgressMock).toHaveBeenCalledTimes(1);
  });

  it("只看本人：标题标明，没有导出按钮", async () => {
    caps.workProgress = "own";
    caps.export = [];
    renderPage();
    expect(await screen.findByText("工作进度表（只看本人）")).toBeTruthy();
    await screen.findByText("小王");
    expect(screen.queryByRole("button", { name: /导出/ })).toBeNull();
  });
});
