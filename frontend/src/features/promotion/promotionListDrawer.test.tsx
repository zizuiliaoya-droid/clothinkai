// @vitest-environment jsdom
// 推广列表点一行开主线抽屉（流程线 8.4）：页签 单据详情 / 金额改动 / 催发记录；「金额改动记录」菜单切到金额改动页签；
// 金额改动页签与菜单只看页级 ui.actions.amount_log（运营没有），不看报价值；详情按 ui.fields 出分组；不出商品编码。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { UiState } from "@/features/flow/keys";
import type { Promotion } from "./types";

const api = vi.hoisted(() => ({
  listPromotions: vi.fn(),
  getPromotion: vi.fn(),
  promotionAmountLog: vi.fn(),
  listPromotionUrgeRecords: vi.fn(),
  getLineEvents: vi.fn(),
}));
vi.mock("@/features/promotion/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/promotion/api")>()),
  listPromotions: api.listPromotions,
  getPromotion: api.getPromotion,
  promotionAmountLog: api.promotionAmountLog,
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

function renderPage(rows: Promotion[], detail: Promotion, pageUi: { actions: UiState["actions"] } = PAGE_UI_AMOUNT) {
  api.listPromotions.mockResolvedValue({ items: rows, total: rows.length, page: 1, page_size: 10, ui: pageUi });
  api.getPromotion.mockResolvedValue(detail);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <PromotionListPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

async function openMenu(): Promise<string[]> {
  fireEvent.click(await screen.findByRole("button", { name: /操作/ }));
  const items = await screen.findAllByRole("menuitem");
  return items.map((el) => el.textContent ?? "");
}

function drawer(): HTMLElement {
  return document.querySelector(".ant-drawer-open .ant-drawer-content") as HTMLElement;
}

describe("PromotionListPage 主线抽屉", () => {
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

  it("报价为空但页级 ui 有 amount_log（管理员 / 主管 / PR）：照样有金额改动页签与菜单项", async () => {
    const row = promotion({ quote_amount: null });
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
    expect(await openMenu()).not.toContain("金额改动记录");
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
    renderPage([promotion({ ui: { actions: {}, edits: [] } })], promotion({ ui: DETAIL_UI }));
    expect((await openMenu()).filter((l) => l.startsWith("催发"))).toEqual([]);
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
