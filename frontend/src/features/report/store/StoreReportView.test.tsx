// @vitest-environment jsdom
// 店铺日明细 / 周汇总 / 月度汇总（7e R4 S6）：三粒度调对接口；日表标题旁「仅退款已录 x / y 天」、
// 周行「已录 x / y 天」；改名后没有「访客数」「支付订单数」；页面文字里没有商品编码；日表行内编辑（S8）。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ReportCapabilities,
  StoreGranularity,
  StoreReport,
  StoreReportRow,
} from "@/features/report/types";

const storeMock = vi.fn();
const updateMock = vi.fn();
const caps = vi.hoisted(() => ({ storeWrite: false }));
vi.mock("@/features/report/api", () => ({
  getStoreReport: (...args: unknown[]) => storeMock(...args),
  updateStoreDaily: (...args: unknown[]) => updateMock(...args),
  getReportCapabilities: () =>
    Promise.resolve({
      work_progress: null,
      publish_progress: null,
      bi: null,
      production: false,
      production_margin: false,
      store_read: true,
      store_write: caps.storeWrite,
      export: [],
      refresh: false,
    } satisfies ReportCapabilities),
  getSummaryFreshness: () =>
    Promise.resolve({
      date_from: "2026-09-02",
      date_to: "2026-09-30",
      source: "live",
      data_as_of: null,
      can_refresh: false,
    }),
  refreshSummaries: () => Promise.resolve(undefined),
  exportReport: () => Promise.resolve("x.xlsx"),
}));
vi.mock("@/features/preference/api", () => ({
  getFilterPreference: () => Promise.resolve({}),
  saveFilterPreference: () => Promise.resolve(undefined),
}));

import { useAuthStore } from "@/stores/authStore";
import { StoreReportView } from "./StoreReportView";

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

// 块体写法：mockReset() 返回 mock 本身，简写箭头会把它当 beforeEach 的返回值，
// vitest 视为清理函数在用例后再调一次 mock（mock 设成拒绝时用例会因此失败）
beforeEach(() => {
  storeMock.mockReset();
  updateMock.mockReset();
  caps.storeWrite = false;
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

function row(over: Partial<StoreReportRow>): StoreReportRow {
  return {
    date_from: "2026-09-02",
    date_to: "2026-09-02",
    visitors: 321,
    pay_amount: "1200.00",
    pay_orders: 12,
    refund_amount: "100.00",
    brushing_amount: "50.00",
    actual_sales: "1050.00",
    ad_spend: "80.00",
    ad_spend_total: null,
    zhitongche_spend: "40.00",
    yinli_spend: null,
    internal_spend: "40.00",
    promo_cost: "60.00",
    total_spend: "100.00",
    live_pay_amount: "300.00",
    live_ad_spend: "20.00",
    refund_only_amount: "10.00",
    total_spend_ratio: "0.0833",
    internal_ratio: "0.0381",
    external_ratio: "0.0571",
    net_spend_ratio: "0.0952",
    net_roi: "10.5000",
    roi_ex_brushing: "11.5000",
    refund_only_rate: "0.0083",
    refund_only_days: 1,
    day_count: 1,
    extra: {},
    ...over,
  };
}

function report(granularity: StoreGranularity, items: StoreReportRow[], over: Partial<StoreReport> = {}): StoreReport {
  return {
    granularity,
    date_from: "2026-09-02",
    date_to: "2026-09-30",
    items,
    recorded_days: 2,
    total_days: 5,
    ...over,
  };
}

function renderView(granularity: StoreGranularity) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/store/x"]}>
        <StoreReportView granularity={granularity} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** 表格主体（排除 rc-table 的测量行）。 */
function tableBody(container: HTMLElement): HTMLElement {
  return container.querySelector(".ant-table-tbody") as HTMLElement;
}

describe("StoreReportView 三粒度调对接口", () => {
  it.each([
    ["day", "店铺日明细"],
    ["week", "店铺周汇总"],
    ["month", "店铺月度汇总"],
  ] as const)("getStoreReport(%s)，标题「%s」", async (granularity, title) => {
    storeMock.mockResolvedValue(report(granularity, [row({})]));
    renderView(granularity);
    await waitFor(() => expect(storeMock).toHaveBeenCalled());
    expect(storeMock.mock.calls.every((c) => c[0] === granularity)).toBe(true);
    expect(storeMock.mock.calls[0][1]).toMatchObject({ preset: "last_30d" });
    expect(await screen.findByRole("heading", { name: title })).toBeTruthy();
  });
});

describe("StoreReportView 已录 x / y 天", () => {
  it("日表：标题旁「仅退款已录 2 / 5 天」；没录的那天仅退款显示「未录」", async () => {
    storeMock.mockResolvedValue(
      report("day", [
        row({}),
        row({
          date_from: "2026-09-03",
          date_to: "2026-09-03",
          refund_only_amount: null,
          refund_only_rate: null,
          refund_only_days: 0,
        }),
      ])
    );
    const { container } = renderView("day");
    expect(await screen.findByText("仅退款已录 2 / 5 天")).toBeTruthy();
    const body = tableBody(container);
    await within(body).findByText("2026-09-03");
    expect(within(body).getByText("未录")).toBeTruthy();
    expect(within(body).getAllByText("¥10.00").length).toBeGreaterThan(0);
  });

  it("周表：行上「已录 1 / 3 天」，周期「09-02 ~ 09-06」；倒序（最新在上）", async () => {
    storeMock.mockResolvedValue(
      report("week", [
        row({
          date_from: "2026-09-02",
          date_to: "2026-09-06",
          refund_only_days: 1,
          day_count: 3,
        }),
        row({
          date_from: "2026-09-07",
          date_to: "2026-09-13",
          refund_only_amount: null,
          refund_only_rate: null,
          refund_only_days: 0,
          day_count: 2,
        }),
      ])
    );
    const { container } = renderView("week");
    const body = tableBody(container);
    expect(await within(body).findByText("已录 1 / 3 天")).toBeTruthy();
    expect(within(body).getByText("已录 0 / 2 天")).toBeTruthy();
    const periods = Array.from(body.querySelectorAll("tr.ant-table-row")).map(
      (tr) => tr.querySelector("td")?.textContent
    );
    expect(periods).toEqual(["09-07 ~ 09-13", "09-02 ~ 09-06"]);
  });

  it("月表：整月显示「YYYY-MM」，不完整的月带实际起止", async () => {
    storeMock.mockResolvedValue(
      report("month", [
        row({ date_from: "2026-09-02", date_to: "2026-09-30", day_count: 20 }),
        row({ date_from: "2026-08-01", date_to: "2026-08-31", day_count: 31 }),
      ])
    );
    const { container } = renderView("month");
    const body = tableBody(container);
    expect(await within(body).findByText("2026-09（09-02 ~ 09-30）")).toBeTruthy();
    expect(within(body).getByText("2026-08")).toBeTruthy();
    expect(within(body).getByText("已录 1 / 20 天")).toBeTruthy();
  });
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}
/** 下钻用：/store/x 渲染视图，其他路径显示当前地址。 */
function renderWithRoutes(granularity: StoreGranularity) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/store/x"]}>
        <Routes>
          <Route path="/store/x" element={<StoreReportView granularity={granularity} />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}
// 2026-09-02 是周三：区间从周三开始，月行和首个周行的实际起点都是 09-02，不是 09-01 / 08-31
const MONTH_ROW = { date_from: "2026-09-02", date_to: "2026-09-30", day_count: 20 };
const WEEK_ROW = { date_from: "2026-09-02", date_to: "2026-09-06", day_count: 3 };
describe("StoreReportView 下钻（7e R4 S9）", () => {
  it("月表：周期链接到周表，带行上实际起止", async () => {
    storeMock.mockResolvedValue(report("month", [row(MONTH_ROW)]));
    const { container } = renderView("month");
    const link = await within(tableBody(container)).findByRole("link", {
      name: "2026-09（09-02 ~ 09-30）",
    });
    expect(link.getAttribute("href")).toBe(
      "/store/weekly?preset=custom&date_from=2026-09-02&date_to=2026-09-30"
    );
  });
  it("周表：周期链接到日表，带行上实际起止", async () => {
    storeMock.mockResolvedValue(report("week", [row(WEEK_ROW)]));
    const { container } = renderView("week");
    const link = await within(tableBody(container)).findByRole("link", { name: "09-02 ~ 09-06" });
    expect(link.getAttribute("href")).toBe(
      "/store/daily?preset=custom&date_from=2026-09-02&date_to=2026-09-06"
    );
  });
  it("日表：周期不是链接、点行不跳", async () => {
    storeMock.mockResolvedValue(report("day", [row({})]));
    const { container } = renderWithRoutes("day");
    const body = tableBody(container);
    fireEvent.click(await within(body).findByText("2026-09-02"));
    expect(within(body).queryAllByRole("link")).toHaveLength(0);
    expect(screen.queryByTestId("location")).toBeNull();
  });
  it.each([
    ["month", MONTH_ROW, "/store/weekly?preset=custom&date_from=2026-09-02&date_to=2026-09-30"],
    ["week", WEEK_ROW, "/store/daily?preset=custom&date_from=2026-09-02&date_to=2026-09-06"],
  ] as const)("%s 表：点整行（非链接格）同样跳下一级", async (granularity, over, href) => {
    storeMock.mockResolvedValue(report(granularity, [row(over)]));
    const { container } = renderWithRoutes(granularity);
    const body = tableBody(container);
    fireEvent.click(await within(body).findByText("¥1200.00"));
    expect((await screen.findByTestId("location")).textContent).toBe(href);
  });
  it("点周期链接跳下一级", async () => {
    storeMock.mockResolvedValue(report("week", [row(WEEK_ROW)]));
    const { container } = renderWithRoutes("week");
    fireEvent.click(await within(tableBody(container)).findByRole("link", { name: "09-02 ~ 09-06" }));
    expect((await screen.findByTestId("location")).textContent).toBe(
      "/store/daily?preset=custom&date_from=2026-09-02&date_to=2026-09-06"
    );
  });
});
describe("StoreReportView 无权限（7e R4 S7）", () => {
  it.each([
    ["day", "店铺日明细"],
    ["week", "店铺周汇总"],
    ["month", "店铺月度汇总"],
  ] as const)("%s 接口 403：显示「没有权限查看%s」，不出表格", async (granularity, title) => {
    storeMock.mockRejectedValue(
      Object.assign(new Error("403"), { response: { status: 403, data: { code: "PERMISSION_DENIED" } } })
    );
    const { container } = renderView(granularity);
    expect(await screen.findByText(`没有权限查看${title}`)).toBeTruthy();
    expect(container.querySelector(".ant-table")).toBeNull();
    expect(screen.queryByText(/仅退款已录/)).toBeNull();
  });
});

describe("StoreReportView 列名与页面文字", () => {
  it("「商品访客数合计」带说明；没有「访客数」「支付订单数」与商品编码", async () => {
    storeMock.mockResolvedValue(report("day", [row({})]));
    const { container } = renderView("day");
    await within(tableBody(container)).findByText("2026-09-02");
    expect(screen.getAllByText("商品访客数合计").length).toBeGreaterThan(0);
    expect(container.textContent).toContain("同一访客看了多个商品会重复计");
    expect(screen.queryAllByText("访客数")).toHaveLength(0);
    expect(container.textContent).not.toContain("支付订单数");
    expect(screen.getAllByText("支付件数").length).toBeGreaterThan(0);
    expect(container.textContent).not.toContain("商品编码");
    // 其中直播两列只展示
    expect(screen.getAllByText("其中直播支付").length).toBeGreaterThan(0);
    expect(screen.getAllByText("其中直播站内花费").length).toBeGreaterThan(0);
  });
});

describe("StoreReportView 行内编辑（7e R4 S8）", () => {
  const DAY = "2026-09-02";
  const dayRows = () => [
    row({}),
    row({
      date_from: "2026-09-03",
      date_to: "2026-09-03",
      zhitongche_spend: null,
      refund_only_amount: null,
      refund_only_days: 0,
    }),
  ];
  const input = (field: string, day = DAY) =>
    screen.getByRole("spinbutton", { name: `${day} ${field}` }) as HTMLInputElement;
  const saveButton = () => screen.getByRole("button", { name: `保存 ${DAY}` });

  async function startEdit() {
    caps.storeWrite = true;
    storeMock.mockResolvedValue(report("day", dayRows()));
    const utils = renderView("day");
    fireEvent.click(await screen.findByRole("button", { name: `编辑 ${DAY}` }));
    await screen.findByRole("spinbutton", { name: `${DAY} 直通车消耗` });
    return utils;
  }

  it("没有 store_write：日表不出编辑按钮与操作列", async () => {
    storeMock.mockResolvedValue(report("day", dayRows()));
    const { container } = renderView("day");
    await within(tableBody(container)).findByText("2026-09-03");
    // 等 capabilities 回来再断言「不出」
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryAllByRole("button", { name: /^编辑/ })).toHaveLength(0);
    expect(container.querySelector("thead")?.textContent).not.toContain("操作");
  });

  it("能写也只在日表出：周表没有编辑", async () => {
    caps.storeWrite = true;
    storeMock.mockResolvedValue(report("week", [row({ date_to: "2026-09-06", day_count: 3 })]));
    const { container } = renderView("week");
    await within(tableBody(container)).findByText("09-02 ~ 09-06");
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryAllByRole("button", { name: /^编辑/ })).toHaveLength(0);
  });

  it("能写：每个日行一个编辑按钮；进入编辑后四个金额框带原值，一次只编辑一行", async () => {
    await startEdit();
    expect(input("全站推消耗").value).toBe("");
    // InputNumber 显示时去掉末尾的 0（40.00 → 40），数值不变、不算改动
    expect(input("直通车消耗").value).toBe("40");
    expect(input("引力魔方消耗").value).toBe("");
    expect(input("仅退款").value).toBe("10");
    // 另一行的编辑按钮在编辑时禁用
    expect(
      (screen.getByRole("button", { name: "编辑 2026-09-03" }) as HTMLButtonElement).disabled
    ).toBe(true);
  });

  it("保存只发改了的字段；成功后重新查询并退出编辑", async () => {
    updateMock.mockResolvedValue({ ok: true, date: DAY });
    await startEdit();
    const before = storeMock.mock.calls.length;
    fireEvent.change(input("直通车消耗"), { target: { value: "45" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    expect(updateMock).toHaveBeenCalledWith(DAY, { zhitongche_spend: "45" });
    await waitFor(() => expect(storeMock.mock.calls.length).toBeGreaterThan(before));
    expect(await screen.findByRole("button", { name: `编辑 ${DAY}` })).toBeTruthy();
    expect(screen.queryByRole("spinbutton", { name: `${DAY} 直通车消耗` })).toBeNull();
  });

  it("清空发 null", async () => {
    updateMock.mockResolvedValue({ ok: true, date: DAY });
    await startEdit();
    fireEvent.change(input("仅退款"), { target: { value: "" } });
    fireEvent.click(saveButton());
    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    expect(updateMock).toHaveBeenCalledWith(DAY, { refund_only_amount: null });
  });

  it("没改动、金额不合法时保存禁用，并提示校验文案", async () => {
    await startEdit();
    expect((saveButton() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(input("引力魔方消耗"), { target: { value: "-1" } });
    expect(await screen.findByText("金额需在 0 ~ 99,999,999.99 之间，最多两位小数")).toBeTruthy();
    expect(input("引力魔方消耗").getAttribute("aria-invalid")).toBe("true");
    expect((saveButton() as HTMLButtonElement).disabled).toBe(true);
    fireEvent.keyDown(input("引力魔方消耗"), { key: "Enter" });
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("422：页面上显示后端提示，留在编辑态", async () => {
    updateMock.mockRejectedValue({
      response: {
        status: 422,
        data: { code: "STORE_DAILY_FUTURE_DATE", message: "不能录入今天之后的日期" },
      },
    });
    await startEdit();
    fireEvent.change(input("直通车消耗"), { target: { value: "45" } });
    fireEvent.click(saveButton());
    expect(await screen.findByText(/不能录入今天之后的日期/)).toBeTruthy();
    expect(input("直通车消耗").value).toBe("45");
  });

  it("Enter 保存", async () => {
    updateMock.mockResolvedValue({ ok: true, date: DAY });
    await startEdit();
    fireEvent.change(input("全站推消耗"), { target: { value: "12.5" } });
    fireEvent.keyDown(input("全站推消耗"), { key: "Enter" });
    await waitFor(() => expect(updateMock).toHaveBeenCalledWith(DAY, { ad_spend_total: "12.5" }));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: `编辑 ${DAY}` }))
    );
  });

  it("Esc 取消：不发请求，恢复原值，焦点回编辑按钮", async () => {
    await startEdit();
    fireEvent.change(input("直通车消耗"), { target: { value: "45" } });
    fireEvent.keyDown(input("直通车消耗"), { key: "Escape" });
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("button", { name: `编辑 ${DAY}` }))
    );
    expect(updateMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("spinbutton", { name: `${DAY} 直通车消耗` })).toBeNull();
  });

  // 评审 r1 L4：375 宽度下左「日期」右「操作」都固定会把数据列挤没，窄屏（< md）不固定「操作」
  const actionsHeader = (container: HTMLElement) =>
    Array.from(container.querySelectorAll("thead th")).find((th) => th.textContent === "操作");

  it("窄屏（< 768）：「操作」列不固定在右侧", async () => {
    const { container } = await startEdit();
    const th = actionsHeader(container);
    expect(th).toBeTruthy();
    expect(th?.className).not.toContain("ant-table-cell-fix-right");
  });

  it("宽屏（≥ 768）：「操作」列固定在右侧", async () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({
      ...original(query),
      matches: true,
    })) as typeof window.matchMedia;
    try {
      const { container } = await startEdit();
      await waitFor(() =>
        expect(actionsHeader(container)?.className).toContain("ant-table-cell-fix-right")
      );
    } finally {
      window.matchMedia = original;
    }
  });
});