// @vitest-environment jsdom
// 单品投产页（7e R3 S9）：毛利三列按 capabilities.production_margin 显示 / 隐藏；
// 站外加购为 null 显示「—」并有说明；停用链接提示 0 条不显示；页面文字里没有商品编码。
// 7e R6 S7：BI 下钻落点 ?goods_id= 数据到后对该商品开一次趋势，不写记忆；评审 r1 M1：落点不套记忆里的季节。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  ProductionReport,
  ProductionRow,
  ReportCapabilities,
} from "@/features/report/types";

const productionMock = vi.fn();
const capsMock = vi.fn();
const trendMock = vi.fn();
const savePrefMock = vi.fn();
const getPrefMock = vi.fn();
vi.mock("@/features/report/api", () => ({
  getProduction: (...args: unknown[]) => productionMock(...args),
  getReportCapabilities: (...args: unknown[]) => capsMock(...args),
  getReportSeasonOptions: () => Promise.resolve([]),
  getProductionTrend: (...args: unknown[]) => trendMock(...args),
  getSummaryFreshness: () =>
    Promise.resolve({
      date_from: "2026-09-13",
      date_to: "2026-10-12",
      source: "live",
      data_as_of: null,
      can_refresh: false,
    }),
  refreshSummaries: () => Promise.resolve(undefined),
  exportReport: () => Promise.resolve("x.xlsx"),
}));
vi.mock("@/features/preference/api", () => ({
  getFilterPreference: (...args: unknown[]) => getPrefMock(...args),
  saveFilterPreference: (...args: unknown[]) => savePrefMock(...args),
}));

import { useAuthStore } from "@/stores/authStore";
import { ProductionPage } from "./ProductionPage";

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

beforeEach(() => {
  productionMock.mockReset();
  capsMock.mockReset();
  trendMock.mockReset();
  trendMock.mockResolvedValue({ points: [] });
  savePrefMock.mockReset();
  savePrefMock.mockResolvedValue(undefined);
  getPrefMock.mockReset();
  getPrefMock.mockResolvedValue({});
  useAuthStore.setState({
    user: {
      id: "u1",
      username: "u1",
      display_name: null,
      email: null,
      status: "active",
      roles: [],
    },
    isAuthenticated: true,
  });
});

afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null, isAuthenticated: false });
});

const GOODS_CODE = "GC-SECRET-0001";

function row(over: Partial<ProductionRow>): ProductionRow {
  return {
    goods_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    goods_code: GOODS_CODE,
    goods_title: "条纹针织上衣",
    goods_short_name: "条纹上衣",
    is_suit: false,
    style_codes: ["K001"],
    main_image_url: null,
    channel: "普通",
    link_count: 1,
    pay_amount: "500.00",
    refund_amount: "50.00",
    return_rate: "0.1000",
    confirmed_amount: "450.00",
    promo_cost: "100.00",
    ad_spend: "20.00",
    total_spend: "120.00",
    pay_orders: 4,
    add_cart_count: 10,
    add_cart_cost: "12.00",
    ad_add_cart_count: 6,
    ad_add_cart_cost: "3.33",
    offsite_add_cart: null,
    pre_return_roi: "4.17",
    net_roi: "3.75",
    unit_deal_cost: "30.00",
    recent7_pay_amount: "100.00",
    recent7_total_spend: "20.00",
    recent7_net_roi: "5.00",
    goods_cost_total: "303.00",
    gross_profit: "147.00",
    gross_margin_rate: "0.3267",
    extra: {},
    ...over,
  };
}

function report(over: Partial<ProductionReport> = {}): ProductionReport {
  return {
    items: [row({})],
    previous: null,
    recent7_range: { date_from: "2026-10-05", date_to: "2026-10-11" },
    inactive_link_count: 0,
    ...over,
  } as ProductionReport;
}

function caps(productionMargin: boolean): ReportCapabilities {
  return {
    work_progress: null,
    publish_progress: null,
    bi: null,
    production: true,
    production_margin: productionMargin,
    store_read: false,
    store_write: false,
    export: ["production"],
  } as ReportCapabilities;
}

function renderPage(url = "/production") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <ProductionPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** 等到表格出了数据行（行里有商品简称）。 */
async function waitRows() {
  await screen.findAllByText("条纹上衣");
}

describe("ProductionPage 毛利三列按 capabilities.production_margin", () => {
  it("管理员（true）：三列表头都在、毛利有数", async () => {
    capsMock.mockResolvedValue(caps(true));
    productionMock.mockResolvedValue(report());
    renderPage();
    await waitRows();
    // antd 横向滚动表格的表头可能渲染不止一份（测量行），按「至少一个」断言
    expect((await screen.findAllByText("销售货品总成本")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("单品毛利").length).toBeGreaterThan(0);
    expect(screen.getAllByText("单品利润率").length).toBeGreaterThan(0);
    expect(screen.getAllByText("¥303.00").length).toBeGreaterThan(0);
  });

  it("非管理员（false）：三列不渲染", async () => {
    capsMock.mockResolvedValue(caps(false));
    productionMock.mockResolvedValue(report());
    renderPage();
    await waitRows();
    // 等能力回来再断言（加载中本来就不渲染）：导出按钮按同一份能力出现
    expect(await screen.findByRole("button", { name: /导出/ })).toBeTruthy();
    expect(screen.queryAllByText("销售货品总成本")).toHaveLength(0);
    expect(screen.queryAllByText("单品毛利")).toHaveLength(0);
    expect(screen.queryAllByText("单品利润率")).toHaveLength(0);
    // 其余列照常在（排除「整张表没渲染」也算通过）
    expect(screen.getAllByText("净投产比").length).toBeGreaterThan(0);
  });
});

describe("ProductionPage 站外加购为 null", () => {
  it("显示「—」，悬停有说明", async () => {
    capsMock.mockResolvedValue(caps(false));
    productionMock.mockResolvedValue(report());
    renderPage();
    await waitRows();
    const hint = screen.getByTestId("offsite-add-cart-empty");
    expect(hint.textContent).toBe("—");
    fireEvent.mouseEnter(hint);
    expect(await screen.findByText("所选区间千牛与万相台没有同一天的数据")).toBeTruthy();
  });

  it("有数时照出数字、不带说明", async () => {
    capsMock.mockResolvedValue(caps(false));
    productionMock.mockResolvedValue(report({ items: [row({ offsite_add_cart: 7 })] }));
    renderPage();
    await waitRows();
    expect(screen.queryByTestId("offsite-add-cart-empty")).toBeNull();
  });
});

describe("ProductionPage 停用链接提示", () => {
  it("0 条不显示", async () => {
    capsMock.mockResolvedValue(caps(false));
    productionMock.mockResolvedValue(report({ inactive_link_count: 0 }));
    renderPage();
    await waitRows();
    expect(screen.queryByText(/条链接已停用/)).toBeNull();
  });

  it("2 条显示「2 条链接已停用、未计入单品」", async () => {
    capsMock.mockResolvedValue(caps(false));
    productionMock.mockResolvedValue(report({ inactive_link_count: 2 }));
    renderPage();
    await waitRows();
    expect(screen.getByText(/2 条链接已停用、未计入单品/)).toBeTruthy();
  });
});

describe("ProductionPage 无权限（7e R4 S7）", () => {
  it("接口 403：显示「没有权限查看单品投产」，不出表格", async () => {
    capsMock.mockResolvedValue({ ...caps(false), production: false, export: [] });
    productionMock.mockRejectedValue(
      Object.assign(new Error("403"), { response: { status: 403, data: { code: "PERMISSION_DENIED" } } })
    );
    const { container } = renderPage();
    expect(await screen.findByText("没有权限查看单品投产")).toBeTruthy();
    expect(container.querySelector(".ant-table")).toBeNull();
  });
});

describe("ProductionPage 页面文字", () => {
  it("没有商品编码；有渠道标签与近 7 天列组表头", async () => {
    capsMock.mockResolvedValue(caps(true));
    productionMock.mockResolvedValue(
      report({
        items: [row({ channel: "普通", link_count: 2 }), row({ channel: "直播", link_count: 0 })],
      })
    );
    const { container } = renderPage();
    await waitRows();
    expect(container.textContent).not.toContain(GOODS_CODE);
    const table = container.querySelector(".ant-table") as HTMLElement;
    expect(within(table).getAllByText("近 7 天（10-05 ~ 10-11）")).toHaveLength(1);
    expect(within(table).getByText("直播")).toBeTruthy();
    expect(within(table).getByText("2 条链接")).toBeTruthy();
  });
});

describe("ProductionPage BI 下钻落点（7e R6 S7）", () => {
  const G1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const LANDING = `/production?preset=custom&date_from=2026-09-13&date_to=2026-10-12&exclude_brushing=false&goods_id=${G1}`;

  it("URL 带 goods_id：数据到后对该商品开一次趋势、带刷单开关；关掉不再弹、不写记忆", async () => {
    capsMock.mockResolvedValue(caps(false));
    productionMock.mockResolvedValue(
      report({
        items: [
          row({ goods_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", goods_short_name: "别的商品" }),
          row({ channel: "普通" }),
          row({ channel: "直播" }),
        ],
      })
    );
    renderPage(LANDING);
    expect(await screen.findByText("投产趋势 · 条纹上衣")).toBeTruthy();
    expect(productionMock.mock.calls[0][0]).toMatchObject({
      preset: "custom",
      date_from: "2026-09-13",
      date_to: "2026-10-12",
      exclude_brushing: false,
    });
    await waitFor(() => expect(trendMock).toHaveBeenCalled());
    expect(trendMock.mock.calls[0][0]).toBe(G1);
    expect(trendMock.mock.calls[0][1]).toMatchObject({ exclude_brushing: false });

    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByText("投产趋势 · 条纹上衣")).toBeNull());
    // 等足保存节流（600ms）：没有手动改筛选，不写记忆；也不会再弹
    await new Promise((resolve) => setTimeout(resolve, 800));
    expect(screen.queryByText("投产趋势 · 条纹上衣")).toBeNull();
    expect(savePrefMock).not.toHaveBeenCalled();
  });

  it("记忆里有季节：落点请求不带季节（与 BI 同为全季节），照常开趋势；改刷单开关后记忆里的季节不丢", async () => {
    getPrefMock.mockResolvedValue({ preset: "last_7d", season: ["2026秋"], exclude_brushing: true });
    capsMock.mockResolvedValue(caps(false));
    productionMock.mockResolvedValue(report());
    renderPage(LANDING);
    expect(await screen.findByText("投产趋势 · 条纹上衣")).toBeTruthy();
    expect(productionMock).toHaveBeenCalledTimes(1);
    expect(productionMock.mock.calls[0][0]).toMatchObject({ preset: "custom", exclude_brushing: false });
    expect(productionMock.mock.calls[0][0].season).toBeUndefined();
    await waitFor(() => expect(trendMock).toHaveBeenCalled());
    expect(screen.queryAllByText("2026秋")).toHaveLength(0);

    // 关掉趋势，手动改刷单开关：写记忆时季节仍是用户存的「2026秋」
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByText("投产趋势 · 条纹上衣")).toBeNull());
    fireEvent.click(screen.getByRole("switch", { name: "剔除刷单" }));
    await waitFor(() => expect(savePrefMock).toHaveBeenCalled(), { timeout: 2000 });
    expect(savePrefMock).toHaveBeenLastCalledWith(
      "product_roi",
      expect.objectContaining({ season: ["2026秋"], exclude_brushing: true })
    );
  });

  it("goods_id 不在本区间的数据里：不弹趋势", async () => {
    capsMock.mockResolvedValue(caps(false));
    productionMock.mockResolvedValue(
      report({ items: [row({ goods_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb" })] })
    );
    renderPage(LANDING);
    await waitRows();
    expect(screen.queryByText(/投产趋势 ·/)).toBeNull();
    expect(trendMock).not.toHaveBeenCalled();
  });
});
