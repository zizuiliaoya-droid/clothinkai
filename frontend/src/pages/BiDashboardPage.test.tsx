// @vitest-environment jsdom
// BI 看板（7e R1）：BI 改挂 report.bi:read 后，加载失败的提示要指向 BI 看板权限，不再是投产报表权限。
// 7e R6 S7：Tab1 店铺总览（两张卡改名、无商品编码）、刷单开关、下钻按 capabilities（不能点有提示）、
// 下钻链接带区间 / 刷单开关 / goods_id / pr_id（含 unassigned）、刷新后 BI 重新取数。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BiDashboard,
  BiGoodsRow,
  BiWorkloadRow,
  ReportCapabilities,
  ReportFreshness,
} from "@/features/report/types";

const biMock = vi.fn();
const capsMock = vi.fn();
const freshnessMock = vi.fn();
const refreshMock = vi.fn();
const getPrefMock = vi.fn();
const savePrefMock = vi.fn();
vi.mock("@/features/report/api", () => ({
  getBiDashboard: (...args: unknown[]) => biMock(...args),
  getReportCapabilities: (...args: unknown[]) => capsMock(...args),
  getSummaryFreshness: (...args: unknown[]) => freshnessMock(...args),
  refreshSummaries: (...args: unknown[]) => refreshMock(...args),
}));
vi.mock("@/features/preference/api", () => ({
  getFilterPreference: (...args: unknown[]) => getPrefMock(...args),
  saveFilterPreference: (...args: unknown[]) => savePrefMock(...args),
}));

import { useAuthStore } from "@/stores/authStore";
import { BiDashboardPage } from "./BiDashboardPage";

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
const G1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const GOODS_CODE = "GC-SECRET-0001";

function caps(over: Partial<ReportCapabilities> = {}): ReportCapabilities {
  return {
    work_progress: "all",
    publish_progress: "all",
    bi: "full",
    production: true,
    production_margin: false,
    store_read: true,
    store_write: false,
    export: [],
    refresh: false,
    ...over,
  };
}

function workload(over: Partial<BiWorkloadRow> = {}): BiWorkloadRow {
  return {
    pr_id: PR1,
    pr_name: "小王",
    target_count: 10,
    quote_count: 6,
    quote_progress: "0.6000",
    publish_count: 3,
    publish_progress: "0.5000",
    pending_count: 2,
    cancel_count: 1,
    overdue_count: 1,
    ...over,
  };
}

function goodsRow(over: Partial<BiGoodsRow> = {}): BiGoodsRow {
  return {
    goods_id: G1,
    goods_title: "条纹针织上衣",
    goods_short_name: "条纹上衣",
    is_suit: false,
    style_codes: ["K001"],
    main_image_url: null,
    pay_amount: "500.00",
    refund_amount: "50.00",
    return_rate: "0.1000",
    confirmed_amount: "450.00",
    internal_spend: "20.00",
    external_spend: "100.00",
    total_spend: "120.00",
    net_roi: "3.75",
    // 接口不该给编码；假数据多塞一个，确认页面不会把它渲染出来
    ...({ goods_code: GOODS_CODE } as object),
    ...over,
  };
}

function dashboard(over: Partial<BiDashboard> = {}): BiDashboard {
  return {
    date_from: "2026-09-13",
    date_to: "2026-10-12",
    granularity: "day",
    exclude_brushing: true,
    store: {
      summary: {
        pay_amount: "1000.00",
        refund_amount: "100.00",
        return_rate: "0.1000",
        brushing_amount: "50.00",
        actual_sales: "850.00",
        internal_spend: "80.00",
        external_spend: "120.00",
        total_spend: "200.00",
        total_spend_ratio: "0.2000",
        internal_spend_ratio: "0.0941",
        external_spend_ratio: "0.1412",
        net_roi: "4.25",
        roi_ex_brushing: "4.75",
      },
      trend: [
        {
          date_from: "2026-09-13",
          date_to: "2026-09-13",
          pay_amount: "1000.00",
          refund_amount: "100.00",
          actual_sales: "850.00",
          internal_spend: "80.00",
          external_spend: "120.00",
          total_spend: "200.00",
          net_roi: "4.25",
        },
      ],
      promotion: {
        commission_amount: "900.00",
        commission_count: 9,
        published_spend: "400.00",
        published_count: 4,
        unpublished_spend: "300.00",
        unpublished_count: 3,
        cancelled_amount: "200.00",
        cancelled_count: 2,
        publish_rate: "0.4444",
      },
      workload: [workload(), workload({ pr_id: null, pr_name: "未分配", target_count: 0 })],
    },
    goods: {
      items: [goodsRow()],
      roi_top: [{ goods_id: G1, label: "条纹上衣", value: "3.75" }],
      pay_share: [
        { goods_id: G1, label: "条纹上衣", value: "500.00" },
        { goods_id: null, label: "其他", value: "500.00" },
      ],
    },
    ...over,
  };
}

function freshness(over: Partial<ReportFreshness> = {}): ReportFreshness {
  return {
    date_from: "2026-09-13",
    date_to: "2026-10-12",
    source: "live",
    data_as_of: null,
    can_refresh: false,
    ...over,
  };
}

beforeEach(() => {
  biMock.mockReset();
  // 回显请求里的刷单开关：下钻链接用响应上的值
  biMock.mockImplementation((params: { exclude_brushing?: boolean }) =>
    Promise.resolve(dashboard({ exclude_brushing: params.exclude_brushing ?? true }))
  );
  capsMock.mockReset();
  capsMock.mockResolvedValue(caps());
  freshnessMock.mockReset();
  freshnessMock.mockResolvedValue(freshness());
  refreshMock.mockReset();
  refreshMock.mockResolvedValue({ ok: true, date_from: "2026-09-13", date_to: "2026-10-12" });
  getPrefMock.mockReset();
  getPrefMock.mockResolvedValue({});
  savePrefMock.mockReset();
  savePrefMock.mockResolvedValue(undefined);
  useAuthStore.setState({
    user: { id: "u1", username: "u1", display_name: null, email: null, status: "active", roles: [] },
    isAuthenticated: true,
  });
});
afterEach(() => {
  cleanup();
  useAuthStore.setState({ user: null, isAuthenticated: false });
});

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

function renderPage(url = "/bi") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/bi" element={<BiDashboardPage />} />
          <Route path="*" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** 等店铺总览出了数据（单品表里有商品简称）。 */
async function waitData() {
  await screen.findAllByText("条纹上衣");
}

/** 打开所有图表的「查看数据」表格。 */
function openAllTables() {
  screen.getAllByRole("button", { name: "查看数据" }).forEach((b) => fireEvent.click(b));
}

function hrefParams(link: HTMLElement): [string, URLSearchParams] {
  const href = link.getAttribute("href") ?? "";
  const [path, query = ""] = href.split("?");
  return [path, new URLSearchParams(query)];
}

describe("BiDashboardPage 加载失败", () => {
  it("提示检查 BI 看板权限，不再提投产报表权限", async () => {
    biMock.mockRejectedValue(
      Object.assign(new Error("403"), {
        response: { status: 403, data: { code: "PERMISSION_DENIED", message: "没有权限" } },
      })
    );
    renderPage();
    expect(await screen.findByText("BI 数据加载失败")).toBeTruthy();
    expect(screen.getByText(/BI 看板权限/)).toBeTruthy();
    expect(screen.queryByText(/投产报表权限/)).toBeNull();
  });
});

describe("BiDashboardPage 店铺总览", () => {
  it("两张卡改名「推广总额」「约篇服务费」，没有「推广总花费」；页面没有商品编码", async () => {
    const { container } = renderPage();
    await waitData();
    expect(screen.getAllByText("推广总额").length).toBeGreaterThan(0);
    expect(screen.getByText("约篇服务费")).toBeTruthy();
    expect(container.textContent).not.toContain("推广总花费");
    expect(container.textContent).not.toContain(GOODS_CODE);
    // KPI 用新字段：净投产、实际销售额
    expect(screen.getByText("净投产")).toBeTruthy();
    expect(screen.getAllByText("实际销售额").length).toBeGreaterThan(0);
  });

  it("请求带刷单开关（默认 true）；关掉后重新请求、写记忆", async () => {
    renderPage();
    await waitData();
    expect(biMock.mock.calls[0][0]).toMatchObject({ exclude_brushing: true });
    fireEvent.click(screen.getByRole("switch", { name: "剔除刷单" }));
    await waitFor(() =>
      expect(biMock).toHaveBeenLastCalledWith(expect.objectContaining({ exclude_brushing: false }))
    );
    await waitFor(
      () =>
        expect(savePrefMock).toHaveBeenLastCalledWith(
          "bi_dashboard",
          expect.objectContaining({ exclude_brushing: false })
        ),
      { timeout: 2000 }
    );
  });

  it("汇总可刷新：点「刷新」后 BI 重新取数", async () => {
    freshnessMock.mockResolvedValue(
      freshness({ source: "summary", data_as_of: "2026-10-12T08:20:00+08:00", can_refresh: true })
    );
    renderPage();
    await waitData();
    const before = biMock.mock.calls.length;
    fireEvent.click(await screen.findByRole("button", { name: /刷新/ }));
    await waitFor(() => expect(refreshMock).toHaveBeenCalledWith("2026-09-13", "2026-10-12"));
    await waitFor(() => expect(biMock.mock.calls.length).toBeGreaterThan(before));
  });
});

describe("BiDashboardPage 下钻按 capabilities", () => {
  it("production=false：ROI 柱 / 饼 / 单品行的标签不是链接、有「需要投产报表权限」；PR 行仍是链接", async () => {
    capsMock.mockResolvedValue(caps({ production: false, work_progress: "all" }));
    renderPage();
    await waitData();
    // 能力回来后 PR 行才变链接
    await screen.findByRole("link", { name: "小王" });
    openAllTables();
    expect(screen.queryAllByRole("link", { name: "条纹上衣" })).toHaveLength(0);
    expect(screen.getAllByText(/需要投产报表权限/).length).toBeGreaterThan(0);
    // 不可下钻的标签能聚焦（键盘可达提示）
    const hint = screen.getAllByText("条纹上衣").find((el) => el.closest('[tabindex="0"]'));
    expect(hint).toBeTruthy();
    // 鼠标点柱子也不跳
    fireEvent.click(screen.queryAllByTestId("bar")[0] ?? document.body);
    expect(screen.queryByTestId("location")).toBeNull();
  });

  it("work_progress=null：PR 名不是链接、有「需要工作进度权限」", async () => {
    capsMock.mockResolvedValue(caps({ work_progress: null }));
    renderPage();
    await waitData();
    await screen.findAllByRole("link", { name: "条纹上衣" });
    expect(screen.queryByRole("link", { name: "小王" })).toBeNull();
    expect(screen.getAllByText(/需要工作进度权限/).length).toBeGreaterThan(0);
  });

  it("单品链接带区间、刷单开关与 goods_id；「其他」不可点", async () => {
    getPrefMock.mockResolvedValue({ exclude_brushing: false });
    renderPage();
    await waitData();
    const link = (await screen.findAllByRole("link", { name: "条纹上衣" }))[0];
    const [path, params] = hrefParams(link);
    expect(path).toBe("/production");
    expect(params.get("preset")).toBe("custom");
    expect(params.get("date_from")).toBe("2026-09-13");
    expect(params.get("date_to")).toBe("2026-10-12");
    expect(params.get("exclude_brushing")).toBe("false");
    expect(params.get("goods_id")).toBe(G1);
    expect(params.has("season")).toBe(false);
    openAllTables();
    expect(screen.queryByRole("link", { name: "其他" })).toBeNull();
    // ROI 柱 + 饼 + 单品表 三处都是链接
    expect(screen.getAllByRole("link", { name: "条纹上衣" }).length).toBeGreaterThanOrEqual(3);
  });

  it("PR 链接带区间与 pr_id，「未分配」传 unassigned", async () => {
    renderPage();
    await waitData();
    const [path, params] = hrefParams(await screen.findByRole("link", { name: "小王" }));
    expect(path).toBe("/work-progress");
    expect(params.get("preset")).toBe("custom");
    expect(params.get("date_from")).toBe("2026-09-13");
    expect(params.get("date_to")).toBe("2026-10-12");
    expect(params.get("pr_id")).toBe(PR1);
    const [, unassigned] = hrefParams(screen.getByRole("link", { name: "未分配" }));
    expect(unassigned.get("pr_id")).toBe("unassigned");
  });

  it("production=true：点 ROI 柱跳单品投产并带 goods_id", async () => {
    renderPage();
    await waitData();
    await screen.findAllByRole("link", { name: "条纹上衣" });
    const roiCard = screen.getByText("净投产 Top10").closest(".ant-card") as HTMLElement;
    fireEvent.click(within(roiCard).getAllByTestId("bar")[0]);
    const location = await screen.findByTestId("location");
    expect(location.textContent).toContain("/production?");
    expect(location.textContent).toContain(`goods_id=${G1}`);
    expect(location.textContent).toContain("exclude_brushing=true");
  });
});
