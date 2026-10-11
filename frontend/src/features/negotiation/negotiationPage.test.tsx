// @vitest-environment jsdom
// 谈款页列表（流程线 8.3）：8 个 Tab +「超时 N」、列、操作菜单按 row.ui、新建按页级 ui、超时行高亮。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { UiState } from "@/features/flow/keys";
import type { Negotiation, NegotiationItem, NegotiationPage as NegotiationPageData } from "./types";

const listMock = vi.fn();
const countsMock = vi.fn();
const submitMock = vi.fn();
vi.mock("@/features/negotiation/api", () => ({
  listNegotiations: (...args: unknown[]) => listMock(...args),
  negotiationStatusCounts: (...args: unknown[]) => countsMock(...args),
  submitNegotiation: (...args: unknown[]) => submitMock(...args),
  createNegotiation: vi.fn(),
  updateNegotiation: vi.fn(),
  bloggerCooperationHistory: vi.fn(),
}));

import { NegotiationPage } from "@/pages/NegotiationPage";

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

// 花括号不能省：beforeEach 返回的函数会被当成清理函数
beforeEach(() => {
  listMock.mockReset();
  countsMock.mockReset();
  submitMock.mockReset();
  countsMock.mockResolvedValue({});
});
afterEach(cleanup);

const GOODS_CODE = "GM-20261001";
const SKU_CODE = "SKU-7788-BLK-M";

function item(over: Partial<NegotiationItem> = {}): NegotiationItem {
  return {
    id: "i-1",
    style_id: "s-1",
    style_code: "ST-001",
    display_short_name: "飞狐短裙",
    goods_title: "2026秋冬法式复古高腰A字半身裙",
    sku_id: "k-1",
    color: "黑色",
    size: "M",
    style_main_image_url: null,
    ...over,
  };
}

const PR_UI: UiState = {
  actions: { edit: { state: "enabled" }, submit: { state: "enabled" } },
  edits: ["basic"],
};

function row(over: Partial<Negotiation> = {}): Negotiation {
  return {
    id: "n-1",
    line_id: "l-1",
    copied_from_id: null,
    blogger_id: "b-1",
    blogger_nickname: "小美",
    style_id: "s-1",
    style_code: "ST-001",
    style_name: "飞狐短裙",
    goods_main_id: "g-1",
    goods_code: GOODS_CODE,
    goods_title: "2026秋冬法式复古高腰A字半身裙",
    goods_is_suit: false,
    pr_id: "u-pr",
    pr_name: "王小美",
    promotion_id: null,
    promotion_internal_code: null,
    cooperation_mode: "寄拍",
    platform: "小红书",
    scheduled_publish_date: "2026-10-20",
    quote_amount: "300.00",
    remark: null,
    status: "草稿",
    submitted_at: null,
    reviewed_by: null,
    reviewer_name: null,
    reviewed_at: null,
    review_opinion: null,
    final_reviewed_by: null,
    final_reviewer_name: null,
    final_reviewed_at: null,
    final_review_opinion: null,
    receiver_name: null,
    receiver_phone: null,
    receiver_address: null,
    receiver_confirmed_at: null,
    receiver_confirmed_by: null,
    display_short_name: "飞狐短裙",
    items: [item()],
    overdue: false,
    overdue_hours: null,
    receiver_suggestion: null,
    others_in_progress: null,
    ui: PR_UI,
    created_at: "2026-10-10T08:00:00+08:00",
    updated_at: "2026-10-10T08:00:00+08:00",
    ...over,
  };
}

function pageOf(rows: Negotiation[], ui: UiState = { actions: { create: { state: "enabled" } } }): NegotiationPageData {
  return { items: rows, total: rows.length, page: 1, page_size: 20, ui };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NegotiationPage />
    </QueryClientProvider>
  );
}

async function openMenu(index = 0): Promise<string[]> {
  const buttons = await screen.findAllByRole("button", { name: /操作/ });
  fireEvent.click(buttons[index]);
  const items = await screen.findAllByRole("menuitem");
  return items.map((el) => el.textContent ?? "");
}

describe("NegotiationPage", () => {
  it("8 个 Tab（审批未通过），默认不带状态；「超时 N」= 主管待审 + overdue_only", async () => {
    listMock.mockResolvedValue(pageOf([row()]));
    countsMock.mockResolvedValue({ 主管待审: 5, 主管待审超时: 3, 草稿: 1 });
    renderPage();
    for (const label of ["全部", "主管待审", "待老板审核", "老板退回", "待补收货地址", "审批未通过", "已定稿"]) {
      expect((await screen.findAllByText(label)).length).toBeGreaterThan(0);
    }
    expect(screen.getAllByText("草稿").length).toBeGreaterThan(0);
    expect(listMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ status: undefined, overdue_only: undefined, page: 1 })
    );

    fireEvent.click(await screen.findByRole("button", { name: /只看超 36 小时的主管待审（3 条）/ }));
    await waitFor(() =>
      expect(listMock).toHaveBeenLastCalledWith(
        expect.objectContaining({ status: "主管待审", overdue_only: true, page: 1 })
      )
    );
    expect(screen.getByText("只看主管待审超 36 小时")).toBeTruthy();
  });

  it("没有超时不出「超时」角标", async () => {
    listMock.mockResolvedValue(pageOf([row()]));
    countsMock.mockResolvedValue({ 主管待审: 2, 主管待审超时: 0 });
    renderPage();
    expect(await screen.findByText("小美")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /只看超 36 小时/ })).toBeNull();
  });

  it("商品列：简称 + 套装 Tag + 成员颜色尺码；状态 Tag 用界面文案；超时行有文字 Tag 与浅红底；不出编码", async () => {
    listMock.mockResolvedValue(
      pageOf([
        row({
          id: "n-suit",
          goods_is_suit: true,
          display_short_name: "飞狐套装",
          status: "主管待审",
          overdue: true,
          overdue_hours: 40,
          items: [
            item({ sku_id: SKU_CODE }),
            item({ id: "i-2", style_id: "s-2", display_short_name: "飞狐上衣", sku_id: null, color: null, size: null }),
          ],
          ui: { actions: {}, edits: [] },
        }),
        row({ id: "n-single", status: "审核驳回", ui: { actions: { copy: { state: "enabled" } }, edits: [] } }),
      ])
    );
    renderPage();
    expect(await screen.findByText("飞狐套装")).toBeTruthy();
    expect(screen.getByText("套装")).toBeTruthy();
    expect(screen.getByText("飞狐短裙 · 黑色 / M")).toBeTruthy();
    expect(screen.getByText("飞狐上衣 · 颜色尺码未选")).toBeTruthy();
    expect(screen.getByText("黑色 / M")).toBeTruthy();
    expect(screen.getByText("谈款中 · 主管待审")).toBeTruthy();
    expect(screen.getAllByText("审批未通过").length).toBeGreaterThan(0);

    const overdueTag = screen.getByText("超 36 小时");
    const tr = overdueTag.closest("tr");
    expect(tr?.className).toContain("negotiation-row-overdue");
    const single = screen.getByText("黑色 / M").closest("tr");
    expect(single?.className).not.toContain("negotiation-row-overdue");

    expect(document.body.textContent).not.toContain(GOODS_CODE);
    expect(document.body.textContent).not.toContain(SKU_CODE);
    expect(document.body.textContent).not.toContain("ST-001");
  });

  it("操作菜单只列 row.ui 里有的动作，按固定顺序；禁用项把原因写进文字", async () => {
    listMock.mockResolvedValue(
      pageOf([
        row({
          status: "主管待审",
          ui: {
            actions: {
              manager_review: { state: "disabled", reason: "第一级由 PR 主管审核，管理员不能代做" },
            },
            edits: [],
          },
        }),
      ])
    );
    renderPage();
    expect(await openMenu()).toEqual(["主管审核（第一级由 PR 主管审核，管理员不能代做）"]);
    const item = screen.getByRole("menuitem");
    expect(item.getAttribute("aria-disabled")).toBe("true");
  });

  it("草稿本人：编辑、提交审核；提交调接口", async () => {
    listMock.mockResolvedValue(pageOf([row()]));
    submitMock.mockResolvedValue(row({ status: "主管待审" }));
    renderPage();
    expect(await openMenu()).toEqual(["编辑", "提交审核"]);
    fireEvent.click(screen.getByText("提交审核"));
    await waitFor(() => expect(submitMock).toHaveBeenCalledWith("n-1"));
  });

  it("本页所有行都没有动作（财务、运营）：整列「操作」不显示", async () => {
    listMock.mockResolvedValue(
      pageOf([row({ ui: { actions: {}, edits: [] } }), row({ id: "n-2", ui: null })], { actions: {} })
    );
    renderPage();
    expect(await screen.findAllByText("小美")).toHaveLength(2);
    expect(screen.queryByText("操作")).toBeNull();
    expect(screen.queryByRole("button", { name: /操作/ })).toBeNull();
  });

  it("新建谈款按页级 ui：可点 / 禁用 + 原因可见 / 不出现", async () => {
    listMock.mockResolvedValue(pageOf([row()]));
    const first = renderPage();
    const enabled = await screen.findByRole("button", { name: /新建谈款/ });
    expect((enabled as HTMLButtonElement).disabled).toBe(false);
    first.unmount();

    listMock.mockResolvedValue(
      pageOf([row()], { actions: { create: { state: "disabled", reason: "管理员建的谈款没人能定稿" } } })
    );
    const second = renderPage();
    const disabled = await screen.findByRole("button", { name: /新建谈款/ });
    expect((disabled as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("管理员建的谈款没人能定稿")).toBeTruthy();
    second.unmount();

    listMock.mockResolvedValue(pageOf([row()], { actions: {} }));
    renderPage();
    expect(await screen.findByText("小美")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /新建谈款/ })).toBeNull();
  });
});
