// @vitest-environment jsdom
// 结款页（流程线 8.6）：
// - 「导入结款」只看页级 ui.actions.import（PR-5）：财务、管理员有，主管没有；上传成功的提示带上「结果去导入记录里看」
// - 行内「操作」按行级 ui.actions 出（PR-6）：核查通过只在旧单上、驳回在待付款 / 待财务付款；本页都没有动作时整列不显示
// - 填付款金额：实付默认 = 应付，实时显示相差多少，不相等时调整说明必填；422 FLOW_GATE_MISSING 标回调整说明
// - 上传付款凭证：没有日期选择，付款日期 = 今天、系统自动记录
// - 新列（推广编号、合作模式、应付、实付、调整说明、收款码）、单据信息行展开（不含收件 / 发货）、「待核查（旧单）」筛选
// - 增加结算项保留「运费」（过渡期，PR-8 与 R33 同时停用）
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import dayjs from "dayjs";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { UiState } from "@/features/flow/keys";
import type { Settlement, SettlementPage } from "./types";

const api = vi.hoisted(() => ({
  listSettlements: vi.fn(),
  uploadImportFile: vi.fn(),
  reviewSettlement: vi.fn(),
  fillPaymentAmount: vi.fn(),
  uploadPaymentProof: vi.fn(),
  addExtraItem: vi.fn(),
  getLineEvents: vi.fn(),
}));
vi.mock("@/features/finance/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/finance/api")>()),
  listSettlements: api.listSettlements,
  reviewSettlement: api.reviewSettlement,
  fillPaymentAmount: api.fillPaymentAmount,
  uploadPaymentProof: api.uploadPaymentProof,
  addExtraItem: api.addExtraItem,
}));
vi.mock("@/features/import/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/import/api")>()),
  uploadImportFile: api.uploadImportFile,
}));
vi.mock("@/features/flow/api", () => ({
  LINE_EVENTS_ALL: ["line-events"],
  lineEventsKey: (id: string | null) => ["line-events", id],
  getLineEvents: api.getLineEvents,
  postLineComment: vi.fn(),
}));

import { SettlementListPage } from "@/pages/SettlementListPage";

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
beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
});

const GOODS_CODE = "GOODS-CODE-SHOULD-NOT-SHOW";

function settlement(over: Partial<Settlement> = {}): Settlement {
  return {
    id: "s1",
    settlement_no: "JS-20261013-001",
    promotion_id: "p1",
    blogger_id: "b1",
    style_id: "st1",
    pr_id: "u-pr",
    amount: "400.00",
    total_amount: "400.00",
    payment_amount: null,
    payment_date: null,
    payment_proof_attachment_id: null,
    payment_proof_signed_url: null,
    note_title: null,
    remark: null,
    settlement_status: "待付款",
    reviewed_by: null,
    reviewed_at: null,
    review_action: null,
    review_reason: null,
    paid_by: null,
    created_at: "2026-10-13T08:00:00Z",
    updated_at: "2026-10-13T08:00:00Z",
    style_code: "B1102",
    style_name: "飞狐短裙全称",
    blogger_nickname: "小美",
    payment_adjust_note: null,
    promotion_internal_code: "XHS-20261009-001",
    cooperation_mode: "送拍",
    publish_url: "https://www.xiaohongshu.com/explore/abc",
    promotion_remark: "推广备注文字",
    promotion_review_reason: "推广驳回原因文字",
    promotion_resubmit_note: "重提说明文字",
    payment_qr_signed_url: "https://r2.example/qr.png",
    display_short_name: "飞狐短裙",
    line_id: "L1",
    extra_items: [],
    ui: { actions: {} },
    ...over,
  };
}

function renderPage(items: Settlement[] = [], ui: SettlementPage["ui"] = { actions: {} }) {
  api.listSettlements.mockResolvedValue({ items, total: items.length, page: 1, page_size: 20, ui });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <SettlementListPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

async function openMenu(index = 0): Promise<string[]> {
  const buttons = await screen.findAllByRole("button", { name: /操作/ });
  fireEvent.click(buttons[index]);
  const items = await screen.findAllByRole("menuitem");
  return items.map((el) => el.textContent ?? "");
}

function rowUi(actions: NonNullable<UiState["actions"]>): Pick<UiState, "actions"> {
  return { actions };
}

describe("SettlementListPage 导入结款", () => {
  it("页级 ui 没有 import（主管）：不出「导入结款」", async () => {
    renderPage([], { actions: {} });
    await waitFor(() => expect(api.listSettlements).toHaveBeenCalled());
    // 等列表响应落到页面（按钮在响应之后才判）
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.queryByRole("button", { name: /导入结款/ })).toBeNull();
  });

  it("页级 ui 有 import（财务、管理员）：出按钮，上传成功提示去导入记录看结果", async () => {
    api.uploadImportFile.mockResolvedValue({ batch_id: "b1" });
    renderPage([], { actions: { import: { state: "enabled" } } });
    expect(await screen.findByRole("button", { name: /导入结款/ })).toBeTruthy();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["a,b"], "结款.csv", { type: "text/csv" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(api.uploadImportFile).toHaveBeenCalledWith("manual_settlement", file));
    expect(await screen.findByText(/结果在「系统管理 → 导入记录」里看/)).toBeTruthy();
  });
});

describe("SettlementListPage 列与单据信息", () => {
  it("新列：推广编号、合作模式、应付、实付、调整说明、收款码；品名显示简称，不出商品编码", async () => {
    renderPage([
      settlement({
        total_amount: "400.00",
        payment_amount: "200.00",
        payment_adjust_note: "延迟发文，限流",
        style_code: "B1102",
      }),
    ]);
    expect(await screen.findByText("XHS-20261009-001")).toBeTruthy();
    for (const title of ["推广编号", "合作模式", "应付", "实付", "调整说明", "收款码"]) {
      expect(screen.getAllByText(title).length).toBeGreaterThan(0);
    }
    expect(screen.getByText("送拍")).toBeTruthy();
    expect(screen.getByText("¥200.00")).toBeTruthy();
    expect(screen.getByText("延迟发文，限流")).toBeTruthy();
    expect(screen.getByText("飞狐短裙")).toBeTruthy();
    expect(screen.getByAltText("收款码")).toBeTruthy();
    expect(document.body.textContent).not.toContain(GOODS_CODE);
  });

  it("行展开显示单据信息：发布链接、推广备注、推广驳回原因、重提说明", async () => {
    renderPage([settlement()]);
    await screen.findByText("XHS-20261009-001");
    expect(screen.queryByText("推广备注文字")).toBeNull();
    const expander = document.querySelector(".ant-table-row-expand-icon") as HTMLElement;
    fireEvent.click(expander);
    expect(await screen.findByText("推广备注文字")).toBeTruthy();
    expect(screen.getByText("推广驳回原因文字")).toBeTruthy();
    expect(screen.getByText("重提说明文字")).toBeTruthy();
    const link = screen.getByRole("link", { name: "https://www.xiaohongshu.com/explore/abc" });
    expect(link.getAttribute("target")).toBe("_blank");
    expect(document.body.textContent).not.toMatch(/收件|收货|发货/);
  });

  it("点推广编号开主线抽屉（按 line_id 拉时间线）", async () => {
    api.getLineEvents.mockResolvedValue({ events: [], legacy: [], hidden_segments: [], ui: { actions: {} } });
    renderPage([settlement()]);
    fireEvent.click(await screen.findByRole("button", { name: "XHS-20261009-001" }));
    await waitFor(() => expect(api.getLineEvents).toHaveBeenCalledWith("L1"));
  });

  it("状态筛选的「待核查」叫「待核查（旧单）」", async () => {
    renderPage([settlement()]);
    await screen.findByText("XHS-20261009-001");
    fireEvent.mouseDown(screen.getByRole("combobox"));
    expect(await screen.findByText("待核查（旧单）")).toBeTruthy();
  });
});

describe("SettlementListPage 操作按行级 ui", () => {
  it("待付款（财务）：驳回、增加结算项、填付款金额；没有核查通过", async () => {
    renderPage([
      settlement({
        ui: rowUi({
          reject: { state: "enabled" },
          extra_item: { state: "enabled" },
          fill_payment: { state: "enabled" },
        }),
      }),
    ]);
    expect(await openMenu()).toEqual(["驳回", "增加结算项", "填付款金额"]);
  });

  it("旧待核查单：核查通过；自己提交的显示禁用原因", async () => {
    renderPage([
      settlement({
        settlement_status: "待核查",
        ui: rowUi({
          legacy_approve: { state: "disabled", reason: "不能审核自己提交的推广对应的结款单" },
        }),
      }),
    ]);
    expect(await openMenu()).toEqual(["核查通过（不能审核自己提交的推广对应的结款单）"]);
    expect(screen.getByRole("menuitem").getAttribute("aria-disabled")).toBe("true");
  });

  it("本页所有行都没有动作：整列「操作」不显示", async () => {
    renderPage([settlement({ ui: rowUi({}) }), settlement({ id: "s2", ui: null })]);
    expect(await screen.findAllByText("XHS-20261009-001")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /操作/ })).toBeNull();
  });

  it("驳回：原因必填，调接口带原因", async () => {
    api.reviewSettlement.mockResolvedValue(settlement({ settlement_status: "已驳回" }));
    renderPage([settlement({ ui: rowUi({ reject: { state: "enabled" } }) })]);
    await openMenu();
    fireEvent.click(screen.getByText("驳回"));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /确\s*定|OK/ }));
    expect(await within(dialog).findByText("请输入驳回原因")).toBeTruthy();
    expect(api.reviewSettlement).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText("驳回原因"), { target: { value: "收款码不对" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /确\s*定|OK/ }));
    await waitFor(() =>
      expect(api.reviewSettlement).toHaveBeenCalledWith("s1", { action: "reject", review_reason: "收款码不对" })
    );
  });

  it("增加结算项：类型保留「运费」（过渡期）", async () => {
    renderPage([settlement({ ui: rowUi({ extra_item: { state: "enabled" } }) })]);
    await openMenu();
    fireEvent.click(screen.getByText("增加结算项"));
    const dialog = await screen.findByRole("dialog");
    fireEvent.mouseDown(within(dialog).getByRole("combobox"));
    for (const t of ["运费", "赞奖", "其他"]) {
      expect((await screen.findAllByText(t)).length).toBeGreaterThan(0);
    }
  });
});

describe("SettlementListPage 填付款金额", () => {
  async function openAmount(over: Partial<Settlement> = {}) {
    renderPage([settlement({ ui: rowUi({ fill_payment: { state: "enabled" } }), ...over })]);
    await openMenu();
    fireEvent.click(screen.getByText("填付款金额"));
    return screen.findByRole("dialog");
  }

  it("实付默认 = 应付，相等时调整说明可空", async () => {
    api.fillPaymentAmount.mockResolvedValue(settlement({ settlement_status: "待财务付款" }));
    const dialog = await openAmount({ total_amount: "400.00" });
    const input = within(dialog).getByLabelText("实付金额") as HTMLInputElement;
    expect(input.value).toBe("400.00");
    expect(within(dialog).getByText("与应付一致")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: /确\s*定|OK/ }));
    await waitFor(() =>
      expect(api.fillPaymentAmount).toHaveBeenCalledWith("s1", { payment_amount: "400.00", payment_adjust_note: null })
    );
  });

  it("实付 ≠ 应付：实时显示相差，调整说明必填，填了才提交", async () => {
    api.fillPaymentAmount.mockResolvedValue(settlement({ settlement_status: "待财务付款" }));
    const dialog = await openAmount({ total_amount: "400.00" });
    const input = within(dialog).getByLabelText("实付金额") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "200" } });
    expect(await within(dialog).findByText("与应付相差 ¥200.00")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: /确\s*定|OK/ }));
    expect(await within(dialog).findByText("实付与应付不一致，请写调整说明")).toBeTruthy();
    expect(api.fillPaymentAmount).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText("调整说明"), { target: { value: "延迟发文，限流" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /确\s*定|OK/ }));
    await waitFor(() =>
      expect(api.fillPaymentAmount).toHaveBeenCalledWith("s1", {
        payment_amount: "200.00",
        payment_adjust_note: "延迟发文，限流",
      })
    );
  });

  it("后端 422 FLOW_GATE_MISSING（payment_adjust_note）：调整说明标红，弹窗不关", async () => {
    api.fillPaymentAmount.mockRejectedValue({
      response: {
        status: 422,
        data: {
          code: "FLOW_GATE_MISSING",
          message: "缺：调整说明",
          details: { missing: [{ key: "payment_adjust_note", label: "调整说明" }] },
        },
      },
    });
    // 应付看不到（null）时前端不判，交给后端
    const dialog = await openAmount({ total_amount: null });
    fireEvent.change(within(dialog).getByLabelText("实付金额"), { target: { value: "300" } });
    fireEvent.click(within(dialog).getByRole("button", { name: /确\s*定|OK/ }));
    await waitFor(() => expect(api.fillPaymentAmount).toHaveBeenCalled());
    expect(await within(dialog).findByText("必填")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

describe("SettlementListPage 上传付款凭证", () => {
  it("没有付款日期选择，写明今天自动记录；上传不带日期", async () => {
    api.uploadPaymentProof.mockResolvedValue(settlement({ settlement_status: "已付款" }));
    renderPage([
      settlement({ settlement_status: "待财务付款", ui: rowUi({ upload_proof: { state: "enabled" } }) }),
    ]);
    await openMenu();
    fireEvent.click(screen.getByText("上传付款凭证"));
    const dialog = await screen.findByRole("dialog");
    const today = dayjs().format("YYYY-MM-DD");
    expect(within(dialog).getByText(`付款日期：今天（${today}），系统自动记录`)).toBeTruthy();
    expect(dialog.querySelector(".ant-picker")).toBeNull();
    const input = dialog.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["x"], "付款.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    expect(await within(dialog).findByText(/付款\.png/)).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: /确\s*定|OK/ }));
    await waitFor(() => expect(api.uploadPaymentProof).toHaveBeenCalledWith("s1", file));
  });
});
