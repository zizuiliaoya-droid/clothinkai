// @vitest-environment jsdom
// 工作进度页（7e R5 S7）：季节锁定 = 筛选记忆（清空写 []）、URL 季节只本次生效；PR 表新列与「推广成本」改名；
// 导出与下钻带季节；403 显示无权限提示；只看本人没有导出按钮。S8 单品卡片与「查看全部」；S9 周明细（实际起止、行展开下钻）。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  PrWorkProgress,
  ReportCapabilities,
  WorkProgressGoodsPage,
  WorkProgressGoodsRow,
  WorkProgressWeekRow,
} from "@/features/report/types";

const workProgressMock = vi.fn();
const goodsMock = vi.fn();
const weeksMock = vi.fn();
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
  getWorkProgressGoods: (...args: unknown[]) => goodsMock(...args),
  getWorkProgressWeeks: (...args: unknown[]) => weeksMock(...args),
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

function goodsRow(n: number, over: Partial<WorkProgressGoodsRow> = {}): WorkProgressGoodsRow {
  const { pr_id: _prId, pr_name: _prName, ...metrics } = prRow();
  void _prId;
  void _prName;
  return {
    ...metrics,
    goods_id: `g-${String(n).padStart(2, "0")}`,
    goods_title: `商品全称${n}`,
    goods_short_name: `简称${n}`,
    display_name: `简称${n}`,
    quote_count: 100 - n,
    // 接口不该给编码；假数据多塞一个，确认页面不会把它渲染出来
    ...({ goods_code: `SKU-${n}` } as object),
    ...over,
  };
}

const UNASSIGNED_ROW = goodsRow(0, {
  goods_id: null,
  goods_title: null,
  goods_short_name: null,
  display_name: "未归属商品",
});

function goodsPage(
  items: WorkProgressGoodsRow[],
  unassigned: WorkProgressGoodsRow | null,
  over: Partial<WorkProgressGoodsPage> = {}
): WorkProgressGoodsPage {
  return {
    date_from: "2026-10-01",
    date_to: "2026-10-31",
    total: items.length,
    limit: 12,
    offset: 0,
    items,
    unassigned,
    ...over,
  };
}

// 块体写法：mockReset() 返回 mock 本身，简写箭头会被 vitest 当成清理函数
beforeEach(() => {
  workProgressMock.mockReset();
  workProgressMock.mockResolvedValue([prRow()]);
  goodsMock.mockReset();
  goodsMock.mockResolvedValue(goodsPage([], null));
  weeksMock.mockReset();
  weeksMock.mockResolvedValue({ date_from: "2026-10-01", date_to: "2026-10-31", items: [] });
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

/** 13 个以上的商品：卡片只取前 12、「查看全部」按 20 条翻页。 */
const MANY_GOODS = Array.from({ length: 25 }, (_, i) => goodsRow(i + 1));

/** 假单品接口：按 limit / offset 切片；unassigned 每页都在信封里（同后端），前端只在最后一页接上。 */
function serveGoods(all: WorkProgressGoodsRow[], unassigned: WorkProgressGoodsRow | null) {
  goodsMock.mockImplementation((params: { limit: number; offset: number }) =>
    Promise.resolve(
      goodsPage(all.slice(params.offset, params.offset + params.limit), unassigned, {
        total: all.length,
        limit: params.limit,
        offset: params.offset,
      })
    )
  );
}

const cardsRegion = () => screen.findByRole("region", { name: "单品汇总" });
const DRILL_BUTTON = /^查看 .+ 的推广单$/;

describe("WorkProgressPage 单品卡片", () => {
  it("13 个商品只出 12 张卡、不出未归属；请求带 limit 12 与季节；无商品编码", async () => {
    getPrefMock.mockResolvedValue({ season: ["2026秋"] });
    serveGoods(MANY_GOODS.slice(0, 13), UNASSIGNED_ROW);
    renderPage();
    const region = await cardsRegion();
    await within(region).findByRole("button", { name: "查看 简称1 的推广单" });
    expect(within(region).getAllByRole("button", { name: DRILL_BUTTON })).toHaveLength(12);
    expect(region.textContent).toContain("共 13 个商品");
    expect(region.textContent).toContain("¥420.00");
    expect(region.textContent).not.toContain("简称13");
    expect(region.textContent).not.toContain("未归属商品");
    expect(document.body.textContent).not.toContain("SKU-");
    expect(goodsMock.mock.calls[0][0]).toMatchObject({
      limit: 12,
      offset: 0,
      season: ["2026秋"],
    });
  });

  it("点商品名下钻：带 goods_id、区间与季节，不带 pr_id", async () => {
    getPrefMock.mockResolvedValue({ season: ["2026秋"] });
    serveGoods(MANY_GOODS.slice(0, 3), null);
    renderPage();
    const region = await cardsRegion();
    fireEvent.click(await within(region).findByRole("button", { name: "查看 简称2 的推广单" }));
    await waitFor(() => expect(drillMock).toHaveBeenCalled());
    expect(drillMock.mock.calls[0][0]).toEqual({
      date_from: "2026-10-01",
      date_to: "2026-10-31",
      goods_id: "g-02",
      season: ["2026秋"],
      page: 1,
      page_size: 20,
    });
    expect(await screen.findByText("简称2 的推广单")).toBeTruthy();
  });

  it("查看全部：第 1 页 offset 0 无未归属；第 2 页 offset 20，未归属排最后且不可点", async () => {
    serveGoods(MANY_GOODS, UNASSIGNED_ROW);
    renderPage();
    const region = await cardsRegion();
    fireEvent.click(await within(region).findByRole("button", { name: "查看全部" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("button", { name: "查看 简称1 的推广单" });
    expect(goodsMock).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 20, offset: 0 }));
    expect(within(dialog).getAllByRole("button", { name: DRILL_BUTTON })).toHaveLength(20);
    // 说明文字里有「未归属商品」字样，只看表体
    expect(dialog.querySelector(".ant-table-tbody")?.textContent).not.toContain("未归属商品");
    fireEvent.click(within(dialog).getByTitle("2"));
    await within(dialog).findByRole("button", { name: "查看 简称21 的推广单" });
    expect(goodsMock).toHaveBeenLastCalledWith(expect.objectContaining({ limit: 20, offset: 20 }));
    const rows = dialog.querySelectorAll(".ant-table-tbody tr.ant-table-row");
    expect(rows).toHaveLength(6);
    expect(rows[rows.length - 1].textContent).toContain("未归属商品");
    expect(within(dialog).queryByRole("button", { name: "查看 未归属商品 的推广单" })).toBeNull();
    expect(dialog.textContent).not.toContain("SKU-");
  });

  it("没有归属商品（total 0）：卡片区空态，查看全部第一页就是未归属", async () => {
    serveGoods([], UNASSIGNED_ROW);
    renderPage();
    const region = await cardsRegion();
    expect(await within(region).findByText(/区间内的推广单都没有归属商品/)).toBeTruthy();
    fireEvent.click(within(region).getByRole("button", { name: "查看全部" }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(dialog.querySelector(".ant-table-tbody")?.textContent).toContain("未归属商品")
    );
  });

  it("单品接口 403：整页显示无权限提示", async () => {
    goodsMock.mockRejectedValue(
      Object.assign(new Error("403"), { response: { status: 403, data: { code: "PERMISSION_DENIED" } } })
    );
    const { container } = renderPage();
    expect(await screen.findByText("没有权限查看工作进度表")).toBeTruthy();
    expect(container.querySelector(".ant-table")).toBeNull();
    expect(goodsMock).toHaveBeenCalledTimes(1);
  });
  it("单品接口 500：卡片区出错误提示（后端 message），PR 表与周明细照常", async () => {
    goodsMock.mockRejectedValue(
      Object.assign(new Error("500"), {
        response: { status: 500, data: { code: "INTERNAL_ERROR", message: "单品服务暂不可用" } },
      })
    );
    serveWeeks(WEEK_ROWS);
    renderPage();
    const region = await screen.findByRole("region", { name: "单品汇总" });
    // 非 403 重试一次后才落到错误，放宽等待
    const alert = await within(region).findByRole("alert", {}, { timeout: 4000 });
    expect(alert.textContent).toContain("单品服务暂不可用");
    expect(screen.queryByText("没有权限查看工作进度表")).toBeNull();
    expect(await screen.findByText("小王")).toBeTruthy();
    expect(within(await weeksRegion()).queryByRole("alert")).toBeNull();
  });
});function weekRow(week_start: string, date_from: string, date_to: string): WorkProgressWeekRow {
  const { pr_id: _prId, pr_name: _prName, ...metrics } = prRow();
  void _prId;
  void _prName;
  return { ...metrics, week_start, date_from, date_to };
}
/** 区间 2025-12-24（周三）~ 2026-01-09（周五）：首尾周被截断，中间一周跨年。 */
const WEEK_ROWS = [
  weekRow("2025-12-22", "2025-12-24", "2025-12-28"),
  weekRow("2025-12-29", "2025-12-29", "2026-01-04"),
  weekRow("2026-01-05", "2026-01-05", "2026-01-09"),
];
function serveWeeks(items: WorkProgressWeekRow[]) {
  weeksMock.mockResolvedValue({ date_from: "2025-12-24", date_to: "2026-01-09", items });
}
const weeksRegion = () => screen.findByRole("region", { name: "周明细" });
describe("WorkProgressPage 周明细", () => {
  it("周期列按实际起止：首周从周三起、末周到周五止，跨年那周起止带年份；请求带季节", async () => {
    getPrefMock.mockResolvedValue({ season: ["2026秋"] });
    serveWeeks(WEEK_ROWS);
    renderPage();
    const region = await weeksRegion();
    await within(region).findByText("12-24 ~ 12-28");
    expect(within(region).getByText("2025-12-29 ~ 2026-01-04")).toBeTruthy();
    expect(within(region).getByText("01-05 ~ 01-09")).toBeTruthy();
    // 周一（桶键）不当成起日显示
    expect(region.textContent).not.toContain("12-22");
    expect(region.querySelector(".ant-table-thead")?.textContent).toContain("推广成本");
    expect(weeksMock.mock.calls[0][0]).toMatchObject({ season: ["2026秋"] });
  });
  it("展开周行：下钻 = 行上实际起止 + 季节，不限 PR / 商品；再点收起", async () => {
    getPrefMock.mockResolvedValue({ season: ["2026秋"] });
    serveWeeks(WEEK_ROWS);
    renderPage();
    const region = await weeksRegion();
    const toggle = await within(region).findByRole("button", {
      name: "展开 12-24 ~ 12-28 的推广单",
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(drillMock).not.toHaveBeenCalled();
    fireEvent.click(toggle);
    await waitFor(() => expect(drillMock).toHaveBeenCalled());
    expect(drillMock.mock.calls[0][0]).toEqual({
      date_from: "2025-12-24",
      date_to: "2025-12-28",
      season: ["2026秋"],
      page: 1,
      page_size: 20,
    });
    expect(within(region).getByText("合作日期 2025-12-24 至 2025-12-28")).toBeTruthy();
    const collapse = within(region).getByRole("button", { name: "收起 12-24 ~ 12-28 的推广单" });
    expect(collapse.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(collapse);
    await waitFor(() =>
      expect(
        within(region)
          .getByRole("button", { name: "展开 12-24 ~ 12-28 的推广单" })
          .getAttribute("aria-expanded")
      ).toBe("false")
    );
  });
  it("键盘：展开按钮是 Tab 可达的原生按钮（Enter / 空格由浏览器转成点击）", async () => {
    serveWeeks(WEEK_ROWS);
    renderPage();
    const region = await weeksRegion();
    const toggle = await within(region).findByRole("button", {
      name: "展开 2025-12-29 ~ 2026-01-04 的推广单",
    });
    expect(toggle.tagName).toBe("BUTTON");
    expect(toggle.getAttribute("type")).toBe("button");
    expect(toggle.tabIndex).toBeGreaterThanOrEqual(0);
    toggle.focus();
    expect(document.activeElement).toBe(toggle);
    fireEvent.click(toggle);
    await waitFor(() => expect(drillMock).toHaveBeenCalled());
    expect(drillMock.mock.calls[0][0]).toMatchObject({
      date_from: "2025-12-29",
      date_to: "2026-01-04",
    });
    expect(drillMock.mock.calls[0][0]).not.toHaveProperty("season");
  });
  it("周明细接口 403：整页显示无权限提示", async () => {
    weeksMock.mockRejectedValue(
      Object.assign(new Error("403"), { response: { status: 403, data: { code: "PERMISSION_DENIED" } } })
    );
    const { container } = renderPage();
    expect(await screen.findByText("没有权限查看工作进度表")).toBeTruthy();
    expect(container.querySelector(".ant-table")).toBeNull();
    expect(weeksMock).toHaveBeenCalledTimes(1);
  });
  it("周明细接口 500：周明细区出错误提示（无 message 用兜底文字），不出无权限页", async () => {
    weeksMock.mockRejectedValue(
      Object.assign(new Error(""), { response: { status: 500, data: { code: "INTERNAL_ERROR" } } })
    );
    renderPage();
    const region = await weeksRegion();
    const alert = await within(region).findByRole("alert", {}, { timeout: 4000 });
    expect(alert.textContent).toContain("周明细加载失败");
    expect(screen.queryByText("没有权限查看工作进度表")).toBeNull();
    expect(within(await screen.findByRole("region", { name: "单品汇总" })).queryByRole("alert")).toBeNull();
  });
});

describe("WorkProgressPage BI 下钻落点（7e R6 S7）", () => {
  const RANGE = "preset=custom&date_from=2026-10-01&date_to=2026-10-31";

  it("URL 带 pr_id：数据与新鲜度到后开该 PR 的下钻一次；关掉不再弹、不写记忆", async () => {
    workProgressMock.mockResolvedValue([
      prRow({ pr_id: "22222222-2222-4222-8222-222222222222", pr_name: "小李" }),
      prRow(),
    ]);
    renderPage(`/work-progress?${RANGE}&pr_id=${PR1}`);
    expect(await screen.findByText("小王 的推广单")).toBeTruthy();
    await waitFor(() => expect(drillMock).toHaveBeenCalled());
    expect(drillMock.mock.calls[0][0]).toMatchObject({
      date_from: "2026-10-01",
      date_to: "2026-10-31",
      pr_id: PR1,
    });
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByText("小王 的推广单")).toBeNull());
    await waitDebounce();
    expect(screen.queryByText("小王 的推广单")).toBeNull();
    expect(savePrefMock).not.toHaveBeenCalled();
  });

  it("pr_id=unassigned：开「未分配」行的下钻，传 unassigned", async () => {
    workProgressMock.mockResolvedValue([prRow(), prRow({ pr_id: null, pr_name: "未分配" })]);
    renderPage(`/work-progress?${RANGE}&pr_id=unassigned`);
    expect(await screen.findByText("未分配 的推广单")).toBeTruthy();
    await waitFor(() => expect(drillMock).toHaveBeenCalled());
    expect(drillMock.mock.calls[0][0]).toMatchObject({ pr_id: "unassigned" });
  });

  it("pr_id 不在本区间的行里：不弹下钻", async () => {
    renderPage(`/work-progress?${RANGE}&pr_id=33333333-3333-4333-8333-333333333333`);
    await screen.findByText("小王");
    await waitDebounce();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(drillMock).not.toHaveBeenCalled();
  });
});