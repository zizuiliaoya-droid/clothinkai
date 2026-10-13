// @vitest-environment jsdom
// BI 看板（7e R1）：BI 改挂 report.bi:read 后，加载失败的提示要指向 BI 看板权限，不再是投产报表权限。
// 7e R6 S7：Tab1 店铺总览（两张卡改名、无商品编码）、刷单开关、下钻按 capabilities（不能点有提示）、
// 下钻链接带区间 / 刷单开关 / goods_id / pr_id（含 unassigned）、刷新后 BI 重新取数。
// 7e R6 S9：只有 read_own（caps.bi = personal）只渲染个人版、不请求 /bi、无金额；caps.bi = null → 无权限页。
// 7e R6 S8：Tab2 单品分析（URL / 记忆带入 tab 与 goods_id、无有效选择取第一项不写记忆、切 Tab / 选商品写记忆、散点空态与截断）。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  BiDashboard,
  BiGoodsNotes,
  BiGoodsRow,
  BiPersonal,
  BiWorkloadRow,
  ProductionTrendPoint,
  ReportCapabilities,
  ReportFreshness,
} from "@/features/report/types";

const biMock = vi.fn();
const capsMock = vi.fn();
const freshnessMock = vi.fn();
const refreshMock = vi.fn();
const getPrefMock = vi.fn();
const savePrefMock = vi.fn();
const goodsTrendMock = vi.fn();
const goodsNotesMock = vi.fn();
const personalMock = vi.fn();
vi.mock("@/features/report/api", () => ({
  getBiDashboard: (...args: unknown[]) => biMock(...args),
  getReportCapabilities: (...args: unknown[]) => capsMock(...args),
  getSummaryFreshness: (...args: unknown[]) => freshnessMock(...args),
  refreshSummaries: (...args: unknown[]) => refreshMock(...args),
  getBiGoodsTrend: (...args: unknown[]) => goodsTrendMock(...args),
  getBiGoodsNotes: (...args: unknown[]) => goodsNotesMock(...args),
  getBiPersonal: (...args: unknown[]) => personalMock(...args),
}));
vi.mock("@/features/preference/api", () => ({
  getFilterPreference: (...args: unknown[]) => getPrefMock(...args),
  saveFilterPreference: (...args: unknown[]) => savePrefMock(...args),
}));
const toPngMock = vi.fn();
vi.mock("html-to-image", () => ({
  toPng: (...args: unknown[]) => toPngMock(...args),
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

function personal(over: Partial<BiPersonal> = {}): BiPersonal {
  return {
    date_from: "2026-09-13",
    date_to: "2026-10-12",
    pr_name: "小王",
    target_count: 10,
    quote_count: 6,
    quote_progress: "0.6000",
    publish_count: 3,
    publish_progress: "0.5000",
    unpublished_count: 2,
    cancel_count: 1,
    overdue_count: 4,
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
  goodsTrendMock.mockReset();
  goodsTrendMock.mockResolvedValue({ points: [] });
  goodsNotesMock.mockReset();
  goodsNotesMock.mockResolvedValue(notes());
  personalMock.mockReset();
  personalMock.mockResolvedValue(personal());
  toPngMock.mockReset();
  toPngMock.mockResolvedValue("data:image/png;base64,AAAA");
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
  // 全量并行跑时首个完整版渲染偏慢，放宽到 3 秒
  await screen.findAllByText("条纹上衣", undefined, { timeout: 3000 });
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

describe("BiDashboardPage 按 caps.bi 分版", () => {
  it("personal：只渲染个人版，不请求 /bi；计数与进度来自 /bi/personal，页面没有「¥」", async () => {
    capsMock.mockResolvedValue(
      caps({ bi: "personal", work_progress: "own", publish_progress: "own", production: false, store_read: false })
    );
    const { container } = renderPage();
    expect(await screen.findByText("目标篇数")).toBeTruthy();
    await screen.findByText("小王");
    expect(personalMock).toHaveBeenCalledWith({ preset: "last_30d", date_from: undefined, date_to: undefined });
    // 六张计数卡：目标 10、约稿 6（60.0%）、发布 3（50.0%）、未发布 2、取消 1、超时 4
    for (const [title, value] of [
      ["目标篇数", "10 篇"],
      ["约稿量", "6 篇"],
      ["发布量", "3 篇"],
      ["未发布", "2 篇"],
      ["已取消", "1 篇"],
      ["超时未发", "4 篇"],
    ]) {
      const card = screen.getByText(title).closest(".ant-card") as HTMLElement;
      expect(within(card).getByText(value), title).toBeTruthy();
    }
    expect(screen.getByText(/约稿进度 60\.0%/)).toBeTruthy();
    expect(screen.getByText(/发布进度 50\.0%/)).toBeTruthy();
    expect(screen.getByText(/2026-09-13 至 2026-10-12/)).toBeTruthy();
    // 评审 r1 L2：标题栏只有标题和导出按钮，姓名在正文（375 宽时标题不被挤掉）
    const head = screen.getByText("我的推广进度").closest(".ant-card-head") as HTMLElement;
    expect(head.textContent).not.toContain("小王");
    expect(within(head).getByRole("button", { name: /导出图片/ })).toBeTruthy();
    // 完整版的东西都不在：没有 Tab、刷单开关、店铺卡；不请求 /bi 与单品接口
    expect(screen.queryByRole("tab", { name: "店铺总览" })).toBeNull();
    expect(screen.queryByRole("switch", { name: "剔除刷单" })).toBeNull();
    expect(screen.queryByText("推广总额")).toBeNull();
    expect(container.textContent).not.toContain("¥");
    expect(biMock).not.toHaveBeenCalled();
    expect(goodsTrendMock).not.toHaveBeenCalled();
    expect(goodsNotesMock).not.toHaveBeenCalled();
  });

  it("personal：目标为 0 时约稿进度显示「未设目标」，发布进度为空显示「—」", async () => {
    capsMock.mockResolvedValue(caps({ bi: "personal" }));
    personalMock.mockResolvedValue(
      personal({ target_count: 0, quote_count: 0, quote_progress: null, publish_count: 0, publish_progress: null })
    );
    renderPage();
    expect(await screen.findByText(/未设目标/)).toBeTruthy();
    expect(screen.getByText(/发布进度 —/)).toBeTruthy();
  });

  it("personal：URL 带的区间预设传给个人版", async () => {
    capsMock.mockResolvedValue(caps({ bi: "personal" }));
    renderPage("/bi?preset=last_7d");
    await screen.findByText("小王");
    expect(personalMock).toHaveBeenLastCalledWith(expect.objectContaining({ preset: "last_7d" }));
    expect(biMock).not.toHaveBeenCalled();
  });

  it("null：显示无权限页，不请求任何 BI 接口", async () => {
    capsMock.mockResolvedValue(caps({ bi: null }));
    renderPage();
    expect(await screen.findByText("没有权限查看BI 看板")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 0));
    expect(biMock).not.toHaveBeenCalled();
    expect(personalMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("tab", { name: "店铺总览" })).toBeNull();
  });

  it("full：不请求个人版", async () => {
    renderPage();
    await waitData();
    expect(personalMock).not.toHaveBeenCalled();
  });
});

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

const G2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function notes(over: Partial<BiGoodsNotes> = {}): BiGoodsNotes {
  return {
    goods_id: G1,
    date_from: "2026-09-13",
    date_to: "2026-10-12",
    truncated: false,
    points: [],
    ...over,
  };
}

/** 两个商品的看板：G1 条纹上衣、G2 纯色衬衫（净投产为空）。 */
function twoGoods(params: { exclude_brushing?: boolean }) {
  return Promise.resolve(
    dashboard({
      exclude_brushing: params.exclude_brushing ?? true,
      goods: {
        items: [
          goodsRow(),
          goodsRow({
            goods_id: G2,
            goods_title: "纯色棉麻衬衫",
            goods_short_name: "纯色衬衫",
            pay_amount: "800.00",
            net_roi: null,
          }),
        ],
        roi_top: [],
        pay_share: [],
      },
    })
  );
}

/** 等足保存节流（600ms）再看有没有写记忆。 */
const waitDebounce = () => new Promise((resolve) => setTimeout(resolve, 800));

/** 打开商品下拉并点一个选项。 */
async function pickGoods(label: string) {
  const input = screen.getByRole("combobox", { name: "选择商品" });
  const selector = input.closest(".ant-select")?.querySelector(".ant-select-selector");
  fireEvent.mouseDown(selector as Element);
  const option = await waitFor(() => {
    const el = document.querySelector(`.ant-select-item-option[title="${label}"]`);
    expect(el).toBeTruthy();
    return el as HTMLElement;
  });
  fireEvent.click(option);
}

function goodsTab() {
  return screen.getByRole("tab", { name: "单品分析" });
}

describe("BiDashboardPage 单品分析", () => {
  beforeEach(() => {
    biMock.mockImplementation(twoGoods);
  });

  it("URL ?tab=goods&goods_id= 进 Tab2、选中该商品、按 BI 实际起止取趋势与散点；不写记忆", async () => {
    renderPage(`/bi?tab=goods&goods_id=${G2}`);
    await waitFor(() => expect(goodsTrendMock).toHaveBeenCalled());
    expect(goodsTab().getAttribute("aria-selected")).toBe("true");
    expect(goodsTrendMock.mock.calls[0]).toEqual([
      G2,
      {
        preset: "custom",
        date_from: "2026-09-13",
        date_to: "2026-10-12",
        granularity: "day",
        exclude_brushing: true,
      },
    ]);
    await waitFor(() =>
      expect(goodsNotesMock).toHaveBeenCalledWith(G2, { date_from: "2026-09-13", date_to: "2026-10-12" })
    );
    // 单品 KPI 是 G2 的：支付 800、净投产为空显示「—」
    expect(await screen.findByText("¥800.00")).toBeTruthy();
    await waitDebounce();
    expect(savePrefMock).not.toHaveBeenCalled();
  });

  it("记忆里的 tab 与 goods_id 带入 Tab2", async () => {
    getPrefMock.mockResolvedValue({ tab: "goods", goods_id: G2 });
    renderPage();
    await waitFor(() => expect(goodsTrendMock).toHaveBeenCalled());
    expect(goodsTab().getAttribute("aria-selected")).toBe("true");
    expect(goodsTrendMock.mock.calls.every((c) => c[0] === G2)).toBe(true);
  });

  it("goods_id 不在本区间选项里：取第一项、不写记忆", async () => {
    renderPage(`/bi?tab=goods&goods_id=cccccccc-cccc-4ccc-8ccc-cccccccccccc`);
    await waitFor(() => expect(goodsTrendMock).toHaveBeenCalled());
    expect(goodsTrendMock.mock.calls[0][0]).toBe(G1);
    await waitDebounce();
    expect(savePrefMock).not.toHaveBeenCalled();
  });

  it("切 Tab 写记忆（默认商品不写）；选商品写 goods_id 并按新商品取数", async () => {
    renderPage();
    await waitData();
    expect(goodsTrendMock).not.toHaveBeenCalled();
    fireEvent.click(goodsTab());
    await waitFor(() => expect(goodsTrendMock).toHaveBeenCalled());
    expect(goodsTrendMock.mock.calls[0][0]).toBe(G1);
    await waitFor(
      () =>
        expect(savePrefMock).toHaveBeenLastCalledWith(
          "bi_dashboard",
          expect.objectContaining({ tab: "goods" })
        ),
      { timeout: 2000 }
    );
    expect(savePrefMock.mock.calls.at(-1)?.[1]).not.toHaveProperty("goods_id");

    await pickGoods("纯色衬衫");
    await waitFor(() => expect(goodsTrendMock).toHaveBeenLastCalledWith(G2, expect.anything()));
    await waitFor(
      () =>
        expect(savePrefMock).toHaveBeenLastCalledWith(
          "bi_dashboard",
          expect.objectContaining({ tab: "goods", goods_id: G2 })
        ),
      { timeout: 2000 }
    );
  });

  it("散点为空：提示「还没有录 7 天数据的笔记」", async () => {
    renderPage("/bi?tab=goods");
    expect(await screen.findByText("还没有录 7 天数据的笔记")).toBeTruthy();
    expect(screen.queryByText(/只显示最近 500 篇/)).toBeNull();
  });

  it("散点截断：提示「只显示最近 500 篇」；点不可点、悬停说明详情页随时间线上线；promo_cost 为空也画", async () => {
    goodsNotesMock.mockResolvedValue(
      notes({
        truncated: true,
        points: [
          {
            promotion_id: "p1",
            publish_date: "2026-10-01",
            like_count: 120,
            promo_cost: "300.00",
            blogger_nickname: "博主甲",
            platform: "小红书",
            detail_url: null,
          },
          {
            promotion_id: "p2",
            publish_date: "2026-10-03",
            like_count: 80,
            promo_cost: null,
            blogger_nickname: "博主乙",
            platform: "抖音",
            detail_url: null,
          },
        ],
      })
    );
    renderPage("/bi?tab=goods");
    expect(await screen.findByText(/只显示最近 500 篇/)).toBeTruthy();
    const pts = await screen.findAllByTestId("point");
    expect(pts).toHaveLength(2);
    expect(pts[0].getAttribute("style") ?? "").toContain("cursor: default");
    expect(pts[0].querySelector("title")?.textContent).toContain("详情页随推广单时间线上线");
    expect(screen.queryByText("还没有录 7 天数据的笔记")).toBeNull();
  });

  it("金额趋势 / 净投产趋势跟着卡片宽度画，不按固定 720 画（窄卡里不用横向滚动，第 2 轮浏览器复核）", async () => {
    // jsdom 没有 ResizeObserver：桩成 observe 时报一次容器宽 480（1440 宽下这两张卡的正文约 503）
    class FakeResizeObserver {
      cb: ResizeObserverCallback;
      constructor(cb: ResizeObserverCallback) {
        this.cb = cb;
      }
      observe() {
        this.cb([{ contentRect: { width: 480 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      disconnect() {}
      unobserve() {}
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    try {
      const point = (date: string): ProductionTrendPoint => ({
        date,
        pay_amount: "100.00",
        refund_amount: "10.00",
        confirmed_amount: "90.00",
        promo_cost: "6.00",
        ad_spend: "4.00",
        total_spend: "10.00",
        net_roi: "9.00",
      });
      goodsTrendMock.mockResolvedValue({ points: [point("2026-10-11"), point("2026-10-12")] });
      renderPage("/bi?tab=goods");
      const amount = await screen.findByRole("img", { name: "单品金额趋势折线图" });
      expect(amount.getAttribute("width")).toBe("480");
      expect(screen.getByRole("img", { name: "单品净投产趋势折线图" }).getAttribute("width")).toBe("480");
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

// 7e R6 S10：导出图片（html-to-image 已 mock）。toPng 被调时节点里要有水印层，文字 =「登录名 姓名 时间」（偏差 5）；
// 截完移除；文件名 bi-<tab>_<实际起>_<实际止>.png；toPng 失败只提示「导出失败，请重试」。
describe("BiDashboardPage 导出图片", () => {
  interface Shot {
    text: string | null | undefined;
    layerStyle: string;
    cellStyle: string;
    nodeText: string;
  }
  let shots: Shot[];
  let downloads: string[];

  beforeEach(() => {
    shots = [];
    downloads = [];
    toPngMock.mockImplementation((node: HTMLElement) => {
      const layer = node.querySelector<HTMLElement>("[data-bi-watermark]");
      shots.push({
        text: layer?.querySelector("span")?.textContent,
        layerStyle: layer?.getAttribute("style") ?? "",
        cellStyle: layer?.querySelector("span")?.getAttribute("style") ?? "",
        nodeText: node.textContent ?? "",
      });
      return Promise.resolve("data:image/png;base64,AAAA");
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push(this.download);
    });
    useAuthStore.setState({
      user: { id: "u1", username: "zhang", display_name: "张三", email: null, status: "active", roles: [] },
      isAuthenticated: true,
    });
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const exportButton = () => screen.getByRole("button", { name: /导出图片/ });

  it("店铺总览：截当前 Tab，节点里有水印（登录名 姓名 时间、斜 30°、12%、不挡鼠标），截完移除，文件名带实际起止", async () => {
    renderPage();
    await waitData();
    fireEvent.click(exportButton());
    await waitFor(() => expect(downloads).toEqual(["bi-store_2026-09-13_2026-10-12.png"]));
    expect(toPngMock).toHaveBeenCalledTimes(1);
    expect(toPngMock.mock.calls[0][1]).toEqual({ pixelRatio: 2, backgroundColor: "#fff" });
    const [shot] = shots;
    expect(shot.text).toMatch(/^zhang 张三 \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
    expect(shot.layerStyle).toContain("opacity: 0.12");
    expect(shot.layerStyle).toContain("pointer-events: none");
    expect(shot.cellStyle).toContain("rotate(-30deg)");
    // 截的是店铺总览的内容，不是整页筛选栏
    expect(shot.nodeText).toContain("推广总额");
    expect(shot.nodeText).not.toContain("剔除刷单：");
    expect(document.querySelector("[data-bi-watermark]")).toBeNull();
  });

  it("单品分析：截 Tab2，文件名 bi-goods_…", async () => {
    renderPage("/bi?tab=goods");
    // BI 数据到了按钮才可点（没数据时不知道实际起止）
    await waitFor(() => expect((exportButton() as HTMLButtonElement).disabled).toBe(false));
    await waitFor(() => expect(goodsNotesMock).toHaveBeenCalled());
    fireEvent.click(exportButton());
    await waitFor(() => expect(downloads).toEqual(["bi-goods_2026-09-13_2026-10-12.png"]));
    expect(shots[0].nodeText).toContain("还没有录 7 天数据的笔记");
    expect(shots[0].nodeText).not.toContain("推广总额");
  });

  it("toPng 失败：提示「导出失败，请重试」，水印也移除，不下载", async () => {
    toPngMock.mockRejectedValue(new Error("canvas tainted"));
    renderPage();
    await waitData();
    fireEvent.click(exportButton());
    expect(await screen.findByText("导出失败，请重试")).toBeTruthy();
    expect(document.querySelector("[data-bi-watermark]")).toBeNull();
    expect(downloads).toEqual([]);
  });

  it("个人版也能导出：没填姓名时水印用登录名，文件名 bi-personal_…", async () => {
    capsMock.mockResolvedValue(caps({ bi: "personal", work_progress: "own" }));
    useAuthStore.setState({
      user: { id: "u1", username: "wang", display_name: null, email: null, status: "active", roles: ["pr"] },
      isAuthenticated: true,
    });
    renderPage();
    expect(await screen.findByText("6 篇")).toBeTruthy();
    fireEvent.click(exportButton());
    await waitFor(() => expect(downloads).toEqual(["bi-personal_2026-09-13_2026-10-12.png"]));
    expect(shots[0].text).toMatch(/^wang wang \d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
    expect(shots[0].nodeText).toContain("目标篇数");
    expect(document.querySelector("[data-bi-watermark]")).toBeNull();
  });
});