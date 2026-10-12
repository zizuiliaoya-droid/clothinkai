// @vitest-environment jsdom
// 店铺日明细 / 周汇总 / 月度汇总（7e R4 S6）：三粒度调对接口；日表标题旁「仅退款已录 x / y 天」、
// 周行「已录 x / y 天」；改名后没有「访客数」「支付订单数」；页面文字里没有商品编码。
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ReportCapabilities,
  StoreGranularity,
  StoreReport,
  StoreReportRow,
} from "@/features/report/types";

const storeMock = vi.fn();
vi.mock("@/features/report/api", () => ({
  getStoreReport: (...args: unknown[]) => storeMock(...args),
  getReportCapabilities: () =>
    Promise.resolve({
      work_progress: null,
      publish_progress: null,
      bi: null,
      production: false,
      production_margin: false,
      store_read: true,
      store_write: false,
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

beforeEach(() => storeMock.mockReset());
afterEach(cleanup);

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

  it("月表：周期显示「YYYY-MM」", async () => {
    storeMock.mockResolvedValue(
      report("month", [row({ date_from: "2026-09-02", date_to: "2026-09-30", day_count: 20 })])
    );
    const { container } = renderView("month");
    const body = tableBody(container);
    expect(await within(body).findByText("2026-09")).toBeTruthy();
    expect(within(body).getByText("已录 1 / 20 天")).toBeTruthy();
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
