// @vitest-environment jsdom
// 推广列表点一行开主线抽屉（流程线 8.4）：页签 单据详情 / 金额改动 / 催发记录；「金额改动记录」菜单切到金额改动页签；
// 金额改动页签与菜单只看页级 ui.actions.amount_log（运营没有），不看报价值；详情按 ui.fields 出分组；不出商品编码。
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { UiState } from "@/features/flow/keys";
import type { Promotion, PromotionListFilters, PromotionPage } from "./types";

const api = vi.hoisted(() => ({
  listPromotions: vi.fn(),
  getPromotionStageCounts: vi.fn(),
  getPromotion: vi.fn(),
  promotionAmountLog: vi.fn(),
  listPromotionUrgeRecords: vi.fn(),
  getLineEvents: vi.fn(),
  getPromotionFilterOptions: vi.fn(),
  getFilterPreference: vi.fn(),
  saveFilterPreference: vi.fn(),
}));
vi.mock("@/features/promotion/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/promotion/api")>()),
  listPromotions: api.listPromotions,
  getPromotionStageCounts: api.getPromotionStageCounts,
  getPromotion: api.getPromotion,
  promotionAmountLog: api.promotionAmountLog,
  getPromotionFilterOptions: api.getPromotionFilterOptions,
}));
vi.mock("@/features/preference/api", () => ({
  getFilterPreference: api.getFilterPreference,
  saveFilterPreference: api.saveFilterPreference,
}));
vi.mock("@/features/urge/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/urge/api")>()),
  listPromotionUrgeRecords: api.listPromotionUrgeRecords,
}));
vi.mock("@/features/flow/api", () => ({
  LINE_EVENTS_ALL: ["line-events"],
  lineEventsKey: (id: string | null) => ["line-events", id],
  getLineEvents: api.getLineEvents,
  postLineComment: vi.fn(),
}));

import { PromotionListPage } from "@/pages/PromotionListPage";

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
afterEach(cleanup);

const GOODS_CODE = "GOODS-CODE-SHOULD-NOT-SHOW";
const LIST_UI: UiState = { actions: { comment: { state: "enabled" } }, edits: [] };
const DETAIL_UI: UiState = {
  actions: { comment: { state: "enabled" } },
  fields: { goods_items: { state: "read" }, receiver: { state: "edit" }, shipping: { state: "read" } },
};

function promotion(over: Partial<Promotion> = {}): Promotion {
  return {
    id: "p1",
    line_id: "L1",
    internal_code: "XHS-20261009-001",
    style_id: "s1",
    sku_id: null,
    goods_main_id: "g1",
    blogger_id: "b1",
    pr_id: "u1",
    style_code_snapshot: "B1102",
    style_short_name_snapshot: "飞狐短裙",
    display_short_name: "飞狐短裙",
    style_main_image_url: null,
    goods_code: GOODS_CODE,
    goods_is_suit: false,
    goods_title: "飞狐印花半身短裙",
    goods_short_name: "飞狐短裙",
    quote_amount: "800.00",
    cost_snapshot: "60.00",
    cooperation_mode: "送拍",
    return_shipping_fee: null,
    total_promo_cost: null,
    return_waybill: null,
    platform: "小红书",
    cooperation_date: "2026-10-01",
    scheduled_publish_date: "2026-10-20",
    actual_publish_date: null,
    publish_url: null,
    cancel_reason: null,
    recall_reason: null,
    like_count: null,
    collect_count: null,
    comment_count: null,
    metrics_recorded_at: null,
    metrics_signed_url: null,
    brand_comment_attachment_id: null,
    brand_comment_signed_url: null,
    note_title: null,
    remark: null,
    publish_status: "未发布",
    recall_status: "未召回",
    settlement_status: "未核查",
    retro_status: "未开始",
    retro_confirmed_by: null,
    retro_confirmed_at: null,
    retro_content: null,
    reviewed_by: null,
    reviewed_at: null,
    review_action: null,
    review_reason: null,
    review_reason_category: null,
    resubmit_note: null,
    resubmitted_at: null,
    is_active: true,
    created_at: "2026-10-01T10:00:00+08:00",
    updated_at: "2026-10-09T10:00:00+08:00",
    urge_status: null,
    dual_platform: false,
    effective_like_count: null,
    is_hit: false,
    cpl: null,
    source_extra: {},
    payment_qr_attachment_id: null,
    payment_qr_signed_url: null,
    settlement_payment_proof_signed_url: null,
    duplicate_warnings: [],
    receiver_name: "张三",
    receiver_phone: "13800000000",
    receiver_address: "上海市徐汇区漕溪北路 100 号",
    ship_status: "待发货",
    ship_pushed_at: null,
    ship_pushed_by_name: null,
    ship_courier: null,
    ship_waybill: null,
    shipped_at: null,
    items: [],
    legacy_color_spec: null,
    goods_members: [],
    ui: LIST_UI,
    ...over,
  };
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  api.getFilterPreference.mockResolvedValue({});
  api.getPromotionStageCounts.mockResolvedValue({ stages: {}, badges: {}, completed: 0, total: 0 });
  api.saveFilterPreference.mockResolvedValue(undefined);
  api.getPromotionFilterOptions.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 50, selected: null, seasons: [] });
  api.getLineEvents.mockResolvedValue({
    line_id: "L1",
    negotiation: null,
    promotion: { id: "p1", internal_code: "XHS-20261009-001", stage: "待推送仓库" },
    next_step: { text: "等主管确认推送仓库", who: "PR 主管" },
    events: [],
    hidden_segments: [],
    legacy: [],
    truncated: false,
    ui: { actions: { comment: { state: "enabled" } } },
  });
  api.promotionAmountLog.mockResolvedValue([]);
  api.listPromotionUrgeRecords.mockResolvedValue([]);
});

/** 页级 ui：能读金额改动接口（管理员 / 主管 / PR）才有 amount_log。 */
const PAGE_UI_AMOUNT = { actions: { amount_log: { state: "enabled" as const } } };
const PAGE_UI_NONE = { actions: {} };

const TARGET_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const TARGET_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";

function RouteProbe() {
  const location = useLocation();
  const navigate = useNavigate();
  return <><output data-testid="location">{location.search}</output>
    <button onClick={() => navigate(`/promotions?id=${TARGET_B}`)}>切换目标B</button>
    <button onClick={() => navigate("/promotions?keyword=保留")}>移除URL目标</button>
  </>;
}

function renderPage(rows: Promotion[], detail: Promotion, pageUi: PromotionPage["ui"] = PAGE_UI_AMOUNT, url = "/promotions", fetchDetail?: (id: string) => Promise<Promotion>, fetchList?: (filters: PromotionListFilters) => Promise<PromotionPage>) {
  if (fetchList) api.listPromotions.mockImplementation(fetchList);
  else api.listPromotions.mockResolvedValue({ items: rows, total: rows.length, page: 1, page_size: 10, ui: pageUi });
  if (fetchDetail) api.getPromotion.mockImplementation(fetchDetail);
  else api.getPromotion.mockResolvedValue(detail);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[url]}>
      <QueryClientProvider client={client}>
        <PromotionListPage />
        <RouteProbe />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

async function openMenu(): Promise<string[]> {
  const row = (await screen.findAllByText("XHS-20261009-001"))[0].closest("tr")!;
  fireEvent.click(within(row).getByRole("button", { name: /操作/ }));
  const items = await screen.findAllByRole("menuitem");
  return items.map((el) => el.textContent ?? "");
}

function drawer(): HTMLElement {
  return document.querySelector(".ant-drawer-open .ant-drawer-content") as HTMLElement;
}

describe("PromotionListPage 主线抽屉", () => {
  it("id 落点独立取详情，目标不在列表页也打开", async () => {
    const id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
    renderPage([], promotion({ id, ui: DETAIL_UI }), PAGE_UI_AMOUNT, `/promotions?id=${id}`);
    await waitFor(() => expect(drawer()).toBeTruthy());
    expect(api.getPromotion).toHaveBeenCalledWith(id);
    expect(within(drawer()).getByText("单据详情")).toBeTruthy();
  });

  it("筛选变化清筛选URL但保留id，关闭抽屉只移除id", async () => {
    renderPage([], promotion({ id: TARGET_A, ui: DETAIL_UI }), PAGE_UI_AMOUNT,
      `/promotions?id=${TARGET_A}&goods_id=${TARGET_B}&season=春&keyword=保留`);
    await waitFor(() => expect(drawer()).toBeTruthy());
    fireEvent.click(screen.getByRole("checkbox", { name: "未分配 PR" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toContain(`id=${TARGET_A}`));
    expect(screen.getByTestId("location").textContent).not.toContain("goods_id");
    expect(screen.getByTestId("location").textContent).toContain("keyword=");
    fireEvent.click(drawer().querySelector(".ant-drawer-close")!);
    await waitFor(() => expect(screen.getByTestId("location").textContent).not.toContain("id="));
    expect(screen.getByTestId("location").textContent).toContain("keyword=");
  });

  it("id A慢B快时旧响应不覆盖当前B", async () => {
    let resolveA!: (value: Promotion) => void;
    const pendingA = new Promise<Promotion>((resolve) => { resolveA = resolve; });
    renderPage([], promotion(), PAGE_UI_NONE, `/promotions?id=${TARGET_A}`,
      (id) => id === TARGET_A ? pendingA : Promise.resolve(promotion({ id: TARGET_B, line_id: "L-B", display_short_name: "目标B" })));
    await waitFor(() => expect(api.getPromotion).toHaveBeenCalledWith(TARGET_A));
    fireEvent.click(screen.getByRole("button", { name: "切换目标B" }));
    await waitFor(() => expect(drawer()?.textContent).toContain("目标B"));
    await act(async () => { resolveA(promotion({ id: TARGET_A, display_short_name: "旧目标A" })); });
    expect(drawer()?.textContent).toContain("目标B");
    expect(drawer()?.textContent).not.toContain("旧目标A");
  });

  it("移除URL目标后迟到响应不重开", async () => {
    let resolve!: (value: Promotion) => void;
    const pending = new Promise<Promotion>((done) => { resolve = done; });
    renderPage([], promotion(), PAGE_UI_NONE, `/promotions?id=${TARGET_A}`, () => pending);
    await waitFor(() => expect(api.getPromotion).toHaveBeenCalledWith(TARGET_A));
    fireEvent.click(screen.getByRole("button", { name: "移除URL目标" }));
    await act(async () => { resolve(promotion({ id: TARGET_A })); });
    expect(drawer()).toBeNull();
  });

  it.each([403, 404])("详情%d保持列表并提示无法打开", async (status) => {
    renderPage([], promotion(), PAGE_UI_NONE, `/promotions?id=${TARGET_A}`,
      () => Promise.reject({ response: { status } }));
    expect(await screen.findByText(/无法打开推广详情/)).toBeTruthy();
    expect(drawer()).toBeNull();
  });

  it("坏id不请求详情，合法季节和keyword仍用于列表", async () => {
    renderPage([], promotion(), PAGE_UI_NONE, "/promotions?id=bad&season=春&keyword=原入口");
    expect(await screen.findByText("推广编号无效")).toBeTruthy();
    await waitFor(() => expect(api.listPromotions).toHaveBeenCalledWith(expect.objectContaining({ keyword: "原入口", seasons: ["春"] })));
    expect(api.getPromotion).not.toHaveBeenCalled();
  });

  it("URL覆盖记忆且初始不保存，手改新维度才写记忆", async () => {
    api.getFilterPreference.mockResolvedValue({ goods_id: TARGET_A, season: ["记忆季"], pr_id: TARGET_A });
    renderPage([], promotion(), PAGE_UI_NONE, `/promotions?goods_id=${TARGET_B}&season=链接季`);
    await waitFor(() => expect(api.listPromotions).toHaveBeenCalledWith(expect.objectContaining({ goods_main_id: TARGET_B, seasons: ["链接季"], pr_id: undefined })));
    expect(api.saveFilterPreference).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("checkbox", { name: "未分配 PR" }));
    await waitFor(() => expect(api.saveFilterPreference).toHaveBeenCalledWith("promotion_list", { goods_id: TARGET_B, season: ["链接季"], pr_id: "unassigned" }), { timeout: 2000 });
    expect(api.listPromotions).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, unassigned_only: true, pr_id: undefined }));
  });

  it("列表A慢B快，切换筛选后A不能覆盖新结果", async () => {
    let resolveA!: (value: PromotionPage) => void;
    const pendingA = new Promise<PromotionPage>((resolve) => { resolveA = resolve; });
    const newPage: PromotionPage = { items: [promotion({ internal_code: "NEW-B" })], total: 1, page: 1, page_size: 10, ui: PAGE_UI_NONE };
    renderPage([], promotion(), PAGE_UI_NONE, "/promotions", undefined,
      (filters) => filters.unassigned_only ? Promise.resolve(newPage) : pendingA);
    await waitFor(() => expect(api.listPromotions).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("checkbox", { name: "未分配 PR" }));
    expect(await screen.findByText("NEW-B")).toBeTruthy();
    await act(async () => { resolveA({ ...newPage, items: [promotion({ internal_code: "OLD-A" })] }); });
    expect(screen.queryByText("OLD-A")).toBeNull();
    expect(screen.getByText("NEW-B")).toBeTruthy();
  });
  it("点一行开抽屉：三个页签，默认单据详情，详情按 ui.fields 出分组；不出商品编码", async () => {
    renderPage([promotion()], promotion({ ui: DETAIL_UI }));
    fireEvent.click((await screen.findAllByText("XHS-20261009-001"))[0]);
    await waitFor(() => expect(api.getLineEvents).toHaveBeenCalledWith("L1"));
    await waitFor(() => expect(drawer()).toBeTruthy());
    const d = within(drawer());
    expect(await d.findByText(/等主管确认推送仓库/)).toBeTruthy();
    expect(d.getByRole("tab", { name: "单据详情" }).getAttribute("aria-selected")).toBe("true");
    expect(d.getByRole("tab", { name: "金额改动" })).toBeTruthy();
    expect(d.getByRole("tab", { name: "催发记录" })).toBeTruthy();
    await waitFor(() => expect(api.getPromotion).toHaveBeenCalledWith("p1"));
    expect(await d.findByRole("region", { name: "收件" })).toBeTruthy();
    expect(d.getByRole("button", { name: "编辑收件" })).toBeTruthy();
    expect(d.getByText("13800000000")).toBeTruthy();
    // 留言框（ui.actions.comment 可点）
    expect(d.getByLabelText("留言")).toBeTruthy();
    expect(document.body.textContent).not.toContain(GOODS_CODE);
  });

  it("点操作按钮不开抽屉；「金额改动记录」菜单开抽屉并切到金额改动页签；「留言」菜单开抽屉", async () => {
    renderPage([promotion()], promotion({ ui: DETAIL_UI }));
    const labels = await openMenu();
    expect(api.getLineEvents).not.toHaveBeenCalled();
    expect(labels).toContain("留言");
    expect(labels).toContain("金额改动记录");
    fireEvent.click(screen.getByText("金额改动记录"));
    await waitFor(() => expect(drawer()).toBeTruthy());
    const d = within(drawer());
    expect((await d.findByRole("tab", { name: "金额改动" })).getAttribute("aria-selected")).toBe("true");
    await waitFor(() => expect(api.promotionAmountLog).toHaveBeenCalledWith("p1"));
    expect(await d.findByText("这单金额没有改动过")).toBeTruthy();
  });

  it("行无写动作、报价为空但页级 ui 有 amount_log：照样有金额改动页签与菜单项", async () => {
    const row = promotion({ quote_amount: null, ui: { actions: {}, edits: [] } });
    renderPage([row], { ...row, ui: DETAIL_UI });
    expect(await openMenu()).toContain("金额改动记录");
    fireEvent.click(screen.getByText("金额改动记录"));
    await waitFor(() => expect(drawer()).toBeTruthy());
    const d = within(drawer());
    expect((await d.findByRole("tab", { name: "金额改动" })).getAttribute("aria-selected")).toBe("true");
    await waitFor(() => expect(api.promotionAmountLog).toHaveBeenCalledWith("p1"));
  });

  it("页级 ui 没有 amount_log（运营，详情没有收件分组）：报价有值也没有金额改动页签与菜单项、不出收件块", async () => {
    // 报价有值：判定不能看值
    const opsRow = promotion({ receiver_name: null, receiver_phone: null, receiver_address: null, ui: { actions: {}, edits: [] } });
    renderPage(
      [opsRow],
      { ...opsRow, quote_amount: null, ui: { actions: {}, fields: { goods_items: { state: "read" }, shipping: { state: "read" } } } },
      PAGE_UI_NONE
    );
    await screen.findAllByText("XHS-20261009-001");
    expect(screen.queryByRole("columnheader", { name: "操作" })).toBeNull();
    expect(screen.queryByRole("button", { name: /操作/ })).toBeNull();
    fireEvent.click((await screen.findAllByText("XHS-20261009-001"))[0]);
    await waitFor(() => expect(drawer()).toBeTruthy());
    const d = within(drawer());
    expect(await d.findByRole("tab", { name: "单据详情" })).toBeTruthy();
    expect(d.queryByRole("tab", { name: "金额改动" })).toBeNull();
    expect(await d.findByRole("region", { name: "发货" })).toBeTruthy();
    expect(d.queryByRole("region", { name: "收件" })).toBeNull();
    expect(d.queryByText(/报价/)).toBeNull();
  });
});

// 流程线 PR-5（8.4、D15）：「新建推广」「导入站外推广」只看页级 ui.actions.create / import；
// 「改归属商品」只看行 ui.edits 的 goods，「催发」只看行 ui.actions.urge。
describe("PromotionListPage 按 ui 出按钮与菜单项", () => {
  const ENABLED = { state: "enabled" as const };
  it("已完结召回只保留后端精确动作，不出现旧万能召回入口", async () => {
    const current = promotion({ completed_at: "2026-10-21T01:00:00Z", publish_status: "已发布", recall_status: "召回中", ui: { actions: { recall_waybill: ENABLED, recall_result: ENABLED }, edits: [] } });
    renderPage([current], current);
    const labels = await openMenu();
    expect(labels).toContain("上传召回寄回截图");
    expect(labels).toContain("登记召回结果");
    expect(labels).not.toContain("发起召回");
    expect(labels.some((label) => label.startsWith("召回（"))).toBe(false);
  });
  it("R33后续菜单使用摘要自己的ui，不能根据状态猜审核能力", async () => {
    const current = promotion({ badges: ["freight_open"], freight_payment: { id: "f1", status: "已驳回", amount: "30.00", payment_date: null, reject_reason: "重新核对", ui: { actions: { freight_resubmit: ENABLED } } }, ui: { actions: {}, edits: [] } });
    renderPage([current], current);
    const labels = await openMenu();
    expect(labels).toContain("重提寄回运费");
    expect(labels).not.toContain("审核寄回运费");
    expect(screen.getByText("运费单：已驳回")).toBeTruthy();
  });
  it("详情召回/R33按字段能力裁剪，召回已上传图按ID显示占位", async () => {
    const current = promotion({ recall_reason: "尺寸不合", return_shipping_fee: null, recall_waybill_attachment_id: "w1", recall_waybill_signed_url: null, ui: { actions: { recall_result: ENABLED }, fields: { recall_info: { state: "read" } } } });
    renderPage([current], current, PAGE_UI_NONE);
    fireEvent.click((await screen.findAllByText("XHS-20261009-001"))[0]);
    await waitFor(() => expect(drawer()).toBeTruthy());
    const d = within(drawer());
    expect(await d.findByText("尺寸不合")).toBeTruthy();
    expect(d.getByText("已上传寄回截图（暂时无法预览）")).toBeTruthy();
    expect(d.getByRole("button", { name: "登记召回结果" })).toBeTruthy();
    expect(d.queryByRole("region", { name: "寄回运费" })).toBeNull();
  });

  it("页级 ui 没有 create / import（PR、运营）：两个按钮都不出", async () => {
    renderPage([promotion()], promotion({ ui: DETAIL_UI }));
    await screen.findAllByText("XHS-20261009-001");
    expect(screen.queryByRole("button", { name: /新建推广/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /导入站外推广/ })).toBeNull();
  });

  it("页级 ui 有 create / import（主管、管理员）：两个按钮都在", async () => {
    renderPage([promotion()], promotion({ ui: DETAIL_UI }), {
      actions: { ...PAGE_UI_AMOUNT.actions, create: ENABLED, import: ENABLED },
    });
    // 先等列表响应落到页面，按钮在响应之后才判
    await screen.findAllByText("XHS-20261009-001");
    expect(screen.getByRole("button", { name: /新建推广/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /导入站外推广/ })).toBeTruthy();
  });

  it("只有 import：出导入、不出新建", async () => {
    renderPage([promotion()], promotion({ ui: DETAIL_UI }), { actions: { import: ENABLED } });
    await screen.findAllByText("XHS-20261009-001");
    expect(screen.getByRole("button", { name: /导入站外推广/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /新建推广/ })).toBeNull();
  });

  it("行 ui.edits 没有 goods（PR）：菜单没有「改归属商品」", async () => {
    renderPage([promotion({ ui: { actions: {}, edits: ["receiver"] } })], promotion({ ui: DETAIL_UI }));
    const labels = await openMenu();
    expect(labels).toContain("改收件信息");
    expect(labels).not.toContain("改归属商品");
  });

  it("行 ui.edits 有 goods（主管）：菜单有「改归属商品」", async () => {
    renderPage([promotion({ ui: { actions: {}, edits: ["goods"] } })], promotion({ ui: DETAIL_UI }));
    expect(await openMenu()).toContain("改归属商品");
  });

  it("催发按行 ui.actions.urge：没有就不出，可点就出，禁用带原因", async () => {
    renderPage([promotion({ ui: { actions: {}, edits: [] } })], promotion({ ui: DETAIL_UI }), PAGE_UI_NONE);
    await screen.findAllByText("XHS-20261009-001");
    expect(screen.queryByRole("button", { name: /操作/ })).toBeNull();
    cleanup();
    renderPage([promotion({ ui: { actions: { urge: ENABLED }, edits: [] } })], promotion({ ui: DETAIL_UI }));
    expect(await openMenu()).toContain("催发");
    cleanup();
    renderPage(
      [promotion({ ui: { actions: { urge: { state: "disabled", reason: "已发布不用催" } }, edits: [] } })],
      promotion({ ui: DETAIL_UI })
    );
    expect(await openMenu()).toContain("催发（已发布不用催）");
  });
});


describe("PR10 阶段与按钮收尾", () => {
  it("菜单只消费ui，保留禁用原因，隐藏的取消/重提不因状态合适而出现", async () => {
    const row = promotion({ ui: { actions: { publish: { state: "enabled" }, deactivate: { state: "disabled", reason: "寄回运费尚未结清" } }, edits: ["other_info", "return_waybill"] } });
    renderPage([row], row, PAGE_UI_NONE);
    const labels = await openMenu();
    expect(labels).toEqual(["标记发布", "停用（寄回运费尚未结清）", "填寄回单号", "其他信息"]);
    expect(screen.getByRole("menuitem", { name: "停用（寄回运费尚未结清）" }).getAttribute("aria-disabled")).toBe("true");
  });

  it("阶段列/可录文字与蓝底一致，快捷筛选切换列表但计数不带stage/badge/分页", async () => {
    api.getPromotionStageCounts.mockResolvedValue({ stages: {}, badges: { metrics_due: 2 }, completed: 0, total: 2 });
    const row = promotion({ stage: "待录 7 天数据", badges: ["metrics_due"], ui: { actions: {}, edits: [] } });
    renderPage([row], row, PAGE_UI_NONE, "/promotions?keyword=定位");
    const cell = await screen.findByText("满 7 天 · 可录");
    expect(cell.closest("tr")?.classList.contains("promotion-metrics-due")).toBe(true);
    expect(screen.getByRole("columnheader", { name: "当前阶段" })).toBeTruthy();
    const shortcut = await screen.findByRole("button", { name: "满 7 天可录（2）" });
    fireEvent.click(shortcut);
    await waitFor(() => expect(api.listPromotions.mock.calls.at(-1)?.[0]).toMatchObject({ badge: "metrics_due", page: 1, keyword: "定位" }));
    expect(api.getPromotionStageCounts.mock.calls.at(-1)?.[0]).not.toHaveProperty("badge");
    expect(api.getPromotionStageCounts.mock.calls.at(-1)?.[0]).not.toHaveProperty("page");
    fireEvent.click(shortcut);
    await waitFor(() => expect(api.listPromotions.mock.calls.at(-1)?.[0].badge).toBeUndefined());
  });
});


describe("PR10 R1 收件筛选", () => {
  it("电话读取被个人撤销后只显示姓名入口", async () => {
    renderPage([], promotion(), { actions: {}, fields: { receiver_name: { state: "read" } } });
    expect(await screen.findByRole("searchbox", { name: "收件人前缀" })).toBeTruthy();
    expect(screen.queryByRole("searchbox", { name: "电话前缀" })).toBeNull();
  });
  it("输入后权限被撤销时清掉被拒电话条件并恢复列表，保留其他筛选", async () => {
    let revoked = false;
    const fetch = async (filters: PromotionListFilters): Promise<PromotionPage> => {
      if (filters.receiver_phone) {
        revoked = true;
        throw { response: { status: 403, data: { code: "FIELD_PERMISSION_DENIED", message: "电话不可读", details: { fields: ["receiver_phone"] } } } };
      }
      return { items: [], total: 0, page: 1, page_size: 10, ui: { actions: {}, fields: { receiver_name: { state: "read" }, ...(revoked ? {} : { receiver_phone: { state: "read" as const } }) } } };
    };
    renderPage([], promotion(), PAGE_UI_NONE, "/promotions?keyword=保留", undefined, fetch);
    const phone = await screen.findByRole("searchbox", { name: "电话前缀" });
    fireEvent.change(phone, { target: { value: "138" } });
    fireEvent.keyDown(phone, { key: "Enter", code: "Enter", charCode: 13, keyCode: 13 });
    await waitFor(() => expect(revoked).toBe(true));
    await waitFor(() => expect(api.listPromotions.mock.calls.at(-1)?.[0]).toMatchObject({ receiver_phone: undefined, keyword: "保留", page: 1 }));
    expect(await screen.findByRole("searchbox", { name: "收件人前缀" })).toBeTruthy();
    expect(screen.queryByRole("searchbox", { name: "电话前缀" })).toBeNull();
  });
});
