// @vitest-environment jsdom
// 流程线 PR-6 S15（8.4、10.3）：推广列表的审核 / 结款 / 录 7 天数据按行 ui 出，三处写死的「已付款」改掉；
// 结算状态按 3.4 新文案；收款码 / 结款重提 / 传结款截图三个弹窗。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Promotion, PromotionSettlementBlock } from "./types";

const api = vi.hoisted(() => ({
  listPromotions: vi.fn(),
  getPromotion: vi.fn(),
  reviewPromotion: vi.fn(),
  resubmitSettlement: vi.fn(),
  uploadSettlementChat: vi.fn(),
  uploadPaymentQrFile: vi.fn(),
  removePaymentQr: vi.fn(),
  writeRetrospective: vi.fn(),
}));
vi.mock("@/features/promotion/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/promotion/api")>()),
  ...api,
}));
vi.mock("@/features/flow/api", () => ({
  LINE_EVENTS_ALL: ["line-events"],
  lineEventsKey: (id: string | null) => ["line-events", id],
  getLineEvents: vi.fn(),
  postLineComment: vi.fn(),
}));

import { PromotionListPage } from "@/pages/PromotionListPage";
import { RetroModal } from "./components/RetroModal";
import { PaymentQrModal } from "./components/PaymentQrModal";
import { SettlementChatModal } from "./components/SettlementChatModal";
import { SettlementResubmitModal } from "./components/SettlementResubmitModal";

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

const ENABLED = { state: "enabled" as const };

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
    goods_code: "G-CODE",
    goods_is_suit: false,
    goods_title: "飞狐印花半身短裙",
    goods_short_name: "飞狐短裙",
    quote_amount: "400.00",
    cost_snapshot: "60.00",
    cooperation_mode: "送拍",
    return_shipping_fee: null,
    total_promo_cost: null,
    return_waybill: null,
    platform: "小红书",
    cooperation_date: "2026-10-01",
    scheduled_publish_date: "2026-10-05",
    actual_publish_date: "2026-10-06",
    publish_url: "https://www.xiaohongshu.com/x",
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
    publish_status: "已发布",
    recall_status: "未召回",
    settlement_status: "待核查",
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
    badges: [],
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
    existing_settlement_status: null,
    duplicate_warnings: [],
    receiver_name: null,
    receiver_phone: null,
    receiver_address: null,
    ship_status: null,
    ship_pushed_at: null,
    ship_pushed_by_name: null,
    ship_courier: null,
    ship_waybill: null,
    shipped_at: null,
    items: [],
    legacy_color_spec: null,
    goods_members: [],
    ui: { actions: {}, edits: [] },
    ...over,
  };
}

function settlement(over: Partial<PromotionSettlementBlock> = {}): PromotionSettlementBlock {
  return {
    settlement_id: "st1",
    status: "已驳回",
    amount: "400.00",
    extra_items: [],
    total_amount: "400.00",
    payment_amount: null,
    payment_adjust_note: null,
    payment_date: null,
    reject_reason: null,
    ...over,
  };
}

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage(rows: Promotion[]) {
  api.listPromotions.mockResolvedValue({ items: rows, total: rows.length, page: 1, page_size: 10, ui: { actions: {} } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client()}>
        <PromotionListPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

function wrap(node: ReactNode) {
  return <QueryClientProvider client={client()}>{node}</QueryClientProvider>;
}

async function openMenu(): Promise<HTMLElement[]> {
  fireEvent.click(await screen.findByRole("button", { name: /操作/ }));
  return screen.findAllByRole("menuitem");
}

function menuItem(items: HTMLElement[], text: string): HTMLElement {
  const found = items.find((el) => el.textContent === text);
  if (!found) throw new Error(`没有菜单项「${text}」：${items.map((el) => el.textContent).join(" / ")}`);
  return found;
}

const isDisabled = (el: HTMLElement) => el.getAttribute("aria-disabled") === "true";

describe("推广列表 · 录 7 天数据按 ui.actions.metrics", () => {
  it("已结款、ui 可点 → 菜单项可点（不再按 settlement_status === 已付款 挡）", async () => {
    renderPage([promotion({ settlement_status: "已结款", ui: { actions: { metrics: ENABLED }, edits: [] } })]);
    expect(isDisabled(menuItem(await openMenu(), "录 7 天数据"))).toBe(false);
  });

  it("灰显 → 禁用并带接口 hint（YYYY-MM-DD 起可录）", async () => {
    renderPage([
      promotion({
        settlement_status: "已结款",
        ui: { actions: { metrics: { state: "grey", hint: "2026-10-13 起可录" } }, edits: [] },
      }),
    ]);
    expect(isDisabled(menuItem(await openMenu(), "录 7 天数据（2026-10-13 起可录）"))).toBe(true);
  });

  it("ui 里没有 metrics（录过之后隐）→ 没有这个菜单项", async () => {
    renderPage([promotion({ settlement_status: "已结款", retro_status: "待复盘" })]);
    const labels = (await openMenu()).map((el) => el.textContent ?? "");
    expect(labels.some((l) => l.startsWith("录 7 天数据"))).toBe(false);
  });
});

describe("推广列表 · 复盘列读 badges", () => {
  it("badges 有 metrics_due → 待录数据", async () => {
    renderPage([promotion({ settlement_status: "已结款", badges: ["metrics_due", "retro_due"] })]);
    expect(await screen.findByText("待录数据")).toBeTruthy();
  });

  it("已付款（待传结款截图）但 badges 没有 metrics_due → 不出「待录数据」", async () => {
    renderPage([promotion({ settlement_status: "已付款", badges: ["retro_due"] })]);
    expect(await screen.findByText("待传结款截图")).toBeTruthy();
    expect(screen.queryByText("待录数据")).toBeNull();
  });

  it("badges 有 metrics_wait_settle → 满 7 天 · 待结款", async () => {
    renderPage([promotion({ settlement_status: "待付款", badges: ["metrics_wait_settle"] })]);
    expect(await screen.findByText("满 7 天 · 待结款")).toBeTruthy();
  });
});

describe("推广列表 · 结算状态新文案", () => {
  it.each([
    ["待核查", "待主管审核"],
    ["已驳回", "推广驳回 · 待重提"],
    ["待付款", "待财务付款"],
    ["结款驳回", "结款驳回 · 待重提"],
    ["已付款", "待传结款截图"],
    ["已结款", "已结款"],
  ])("%s → %s", async (status, label) => {
    renderPage([promotion({ settlement_status: status })]);
    expect(await screen.findByText(label)).toBeTruthy();
  });
});

describe("推广列表 · 审核按 ui.actions.review", () => {
  it("PR（读）：通过 / 驳回都禁用、带原因", async () => {
    renderPage([promotion({ ui: { actions: { review: { state: "disabled", reason: "需 PR 主管审核" } }, edits: [] } })]);
    const items = await openMenu();
    expect(isDisabled(menuItem(items, "审核通过（需 PR 主管审核）"))).toBe(true);
    expect(isDisabled(menuItem(items, "审核驳回（需 PR 主管审核）"))).toBe(true);
  });

  it("主管、缺收款码：通过禁用「缺：收款码」，驳回可点", async () => {
    renderPage([
      promotion({
        ui: { actions: { review: { state: "enabled", missing: [{ key: "payment_qr", label: "收款码" }] } }, edits: [] },
      }),
    ]);
    const items = await openMenu();
    expect(isDisabled(menuItem(items, "审核通过（缺：收款码）"))).toBe(true);
    expect(isDisabled(menuItem(items, "审核驳回"))).toBe(false);
  });

  it("ui 里没有 review（推广驳回等）：待核查也不出审核项（不再按 settlement_status 推）", async () => {
    renderPage([promotion({ settlement_status: "待核查" })]);
    const labels = (await openMenu()).map((el) => el.textContent ?? "");
    expect(labels.some((l) => l.startsWith("审核"))).toBe(false);
  });

  async function approve(row: Promotion, saved: Promotion) {
    api.reviewPromotion.mockResolvedValue(saved);
    renderPage([row]);
    fireEvent.click(menuItem(await openMenu(), "审核通过"));
    const dialog = await screen.findByRole("dialog");
    return { dialog, ok: () => fireEvent.click(within(dialog).getByRole("button", { name: "审核通过" })) };
  }

  it("确认框写应付与去向；成功提示按 review_outcome（应付 0 → 直接已结款）", async () => {
    const row = promotion({ quote_amount: "0.00", cooperation_mode: "置换", ui: { actions: { review: ENABLED }, edits: [] } });
    const { dialog, ok } = await approve(row, { ...row, settlement_status: "已结款", review_outcome: "settled_directly" });
    expect(within(dialog).getByText("应付 ¥0.00，通过后直接记为已结款")).toBeTruthy();
    ok();
    await waitFor(() => expect(api.reviewPromotion).toHaveBeenCalledWith("p1", { action: "approve" }));
    expect(await screen.findByText("审核通过。应付为 0，已直接记为已结款")).toBeTruthy();
  });

  it("已有导入的已付款结款单：确认框与提示都写转待传结款截图（settlement_status = 已付款 不再误报「置换已结清」）", async () => {
    const row = promotion({ existing_settlement_status: "已付款", ui: { actions: { review: ENABLED }, edits: [] } });
    const { dialog, ok } = await approve(row, {
      ...row,
      settlement_status: "已付款",
      review_outcome: "imported_settlement_paid",
    });
    expect(
      within(dialog).getByText("应付 ¥400.00，已有导入的已付款结款单，通过后转待传结款截图，不再生成结款单"),
    ).toBeTruthy();
    expect(within(dialog).queryByText(/生成结款单给财务/)).toBeNull();
    ok();
    expect(await screen.findByText("审核通过。已有导入的已付款结款单，转待传结款截图")).toBeTruthy();
  });

  it("没有结款单、服务费 > 0：确认框照旧写生成结款单给财务", async () => {
    const row = promotion({ ui: { actions: { review: ENABLED }, edits: [] } });
    const { dialog, ok } = await approve(row, { ...row, settlement_status: "待付款", review_outcome: "settlement_created" });
    expect(within(dialog).getByText("应付 ¥400.00，通过后生成结款单给财务")).toBeTruthy();
    // 关掉确认框（Modal.confirm 挂在 body 上，不关会留给后面的用例）
    ok();
    expect(await screen.findByText("审核通过，已生成结款单，转待财务付款")).toBeTruthy();
  });
});

describe("推广列表 · 结款菜单项", () => {
  it("结款重提 / 传结款截图按 ui.actions；灰显带 hint", async () => {
    renderPage([
      promotion({
        settlement_status: "待付款",
        ui: {
          actions: {
            settlement_resubmit: ENABLED,
            settlement_chat: { state: "grey", hint: "财务付款后上传" },
          },
          edits: [],
        },
      }),
    ]);
    const items = await openMenu();
    expect(isDisabled(menuItem(items, "结款重提"))).toBe(false);
    expect(isDisabled(menuItem(items, "传结款截图（财务付款后上传）"))).toBe(true);
  });

  it("收款码按 edits.payment_qr；换不了但传过 → 查看收款码；都没有 → 不出", async () => {
    renderPage([promotion({ ui: { actions: {}, edits: ["payment_qr"] } })]);
    let labels = (await openMenu()).map((el) => el.textContent);
    expect(labels).toContain("收款码");
    expect(labels).not.toContain("查看收款码");
    cleanup();
    renderPage([promotion({ payment_qr_attachment_id: "a1", settlement_status: "已结款" })]);
    labels = (await openMenu()).map((el) => el.textContent);
    expect(labels).toContain("查看收款码");
    expect(labels).not.toContain("收款码");
    cleanup();
    renderPage([promotion()]);
    labels = (await openMenu()).map((el) => el.textContent);
    expect(labels.some((l) => l?.includes("收款码"))).toBe(false);
  });

  it("「其他信息」弹窗里不再有收款码", async () => {
    renderPage([promotion({ payment_qr_attachment_id: "a1" })]);
    fireEvent.click(menuItem(await openMenu(), "其他信息"));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("其他信息（订单号等）")).toBeTruthy();
    expect(within(dialog).queryByText(/收款码/)).toBeNull();
  });
});

describe("PaymentQrModal", () => {
  it("结款单待财务付款（read）：只读说明、没有上传按钮", async () => {
    api.getPromotion.mockResolvedValue(
      promotion({
        settlement_status: "待付款",
        payment_qr_attachment_id: "a1",
        payment_qr_signed_url: "https://r2.example/qr.png",
        settlement: settlement({ status: "待财务付款" }),
        ui: { actions: {}, fields: { payment_qr: { state: "read" } } },
      })
    );
    render(wrap(<PaymentQrModal target={promotion()} onClose={() => {}} />));
    expect(await screen.findByText("财务付款中，已锁定；码不对请让财务驳回后重传")).toBeTruthy();
    expect(screen.getByAltText("收款码")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /上传收款码|替换收款码/ })).toBeNull();
  });

  it("edit：能上传，上传调代传接口", async () => {
    api.getPromotion.mockResolvedValue(promotion({ ui: { actions: {}, fields: { payment_qr: { state: "edit" } } } }));
    api.uploadPaymentQrFile.mockResolvedValue(promotion());
    const { container } = render(wrap(<PaymentQrModal target={promotion()} onClose={() => {}} />));
    expect(await screen.findByText("尚未上传收款码")).toBeTruthy();
    const input = (container.ownerDocument.querySelector("input[type=file]") as HTMLInputElement);
    const file = new File(["x"], "qr.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(await screen.findByRole("button", { name: /上传收款码/ }));
    await waitFor(() => expect(api.uploadPaymentQrFile).toHaveBeenCalledWith("p1", file));
  });

  it("grey：显示「发布后上传」", async () => {
    api.getPromotion.mockResolvedValue(
      promotion({ publish_status: "未发布", ui: { actions: {}, fields: { payment_qr: { state: "grey", hint: "发布后上传" } } } })
    );
    render(wrap(<PaymentQrModal target={promotion()} onClose={() => {}} />));
    expect(await screen.findByText("发布后上传")).toBeTruthy();
  });
});

describe("SettlementResubmitModal", () => {
  const detail = (over: Partial<Promotion> = {}) =>
    promotion({
      settlement_status: "结款驳回",
      settlement: settlement({ reject_reason: "收款码是别人的" }),
      ui: { actions: {}, fields: { payment_qr: { state: "edit" } } },
      ...over,
    });

  it("只读显示财务驳回原因；说明必填；提交带 note", async () => {
    api.getPromotion.mockResolvedValue(detail({ payment_qr_attachment_id: "a1" }));
    api.resubmitSettlement.mockResolvedValue(detail());
    render(wrap(<SettlementResubmitModal target={{ row: promotion(), action: ENABLED }} onClose={() => {}} />));
    expect(await screen.findByText("收款码是别人的")).toBeTruthy();
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "重提结款" }));
    expect(await within(dialog).findByText("请写重提说明")).toBeTruthy();
    expect(api.resubmitSettlement).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText("重提说明"), { target: { value: "  换了收款码  " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "重提结款" }));
    await waitFor(() => expect(api.resubmitSettlement).toHaveBeenCalledWith("p1", { note: "换了收款码" }));
  });

  it("缺收款码（missing）且还没传：提示缺项、重提按钮禁用", async () => {
    api.getPromotion.mockResolvedValue(detail());
    render(
      wrap(
        <SettlementResubmitModal
          target={{ row: promotion(), action: { state: "enabled", missing: [{ key: "payment_qr", label: "收款码" }] } }}
          onClose={() => {}}
        />
      )
    );
    expect(await screen.findByText("缺：收款码，补齐后才能重提")).toBeTruthy();
    const ok = within(screen.getByRole("dialog")).getByRole("button", { name: "重提结款" }) as HTMLButtonElement;
    expect(ok.disabled).toBe(true);
    expect(screen.getByRole("button", { name: /上传收款码/ })).toBeTruthy();
  });
});

describe("SettlementChatModal", () => {
  it("只读显示实付与调整说明；没选图不提交；选图后上传", async () => {
    api.getPromotion.mockResolvedValue(
      promotion({
        settlement_status: "已付款",
        settlement: settlement({ status: "已付款", payment_amount: "200.00", payment_adjust_note: "延迟发文，原价 400" }),
      })
    );
    api.uploadSettlementChat.mockResolvedValue(promotion({ settlement_status: "已结款" }));
    const { container } = render(wrap(<SettlementChatModal target={promotion()} onClose={() => {}} />));
    expect(await screen.findByText("¥200.00")).toBeTruthy();
    expect(screen.getByText("延迟发文，原价 400")).toBeTruthy();
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "提交，记为已结款" }));
    expect(await within(dialog).findByText("请选择结款聊天截图")).toBeTruthy();
    expect(api.uploadSettlementChat).not.toHaveBeenCalled();
    const input = container.ownerDocument.querySelector("input[type=file]") as HTMLInputElement;
    const file = new File(["x"], "chat.png", { type: "image/png" });
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(within(dialog).getByRole("button", { name: "提交，记为已结款" }));
    await waitFor(() => expect(api.uploadSettlementChat).toHaveBeenCalledWith("p1", file));
  });
});

describe("PR-7 主管复盘与完结只读", () => {
  it("PR的行ui没有写权限时不显示写复盘或确认复盘", async () => {
    renderPage([promotion({ retro_status: "待复盘", ui: { actions: {}, edits: [] } })]);
    const labels = (await openMenu()).map((item) => item.textContent);
    expect(labels.some((label) => label?.includes("写复盘"))).toBe(false);
    expect(labels.some((label) => label?.includes("确认复盘"))).toBe(false);
  });

  it("主管按独立ui动作写复盘，已完成版管理员按修订动作进入", async () => {
    renderPage([promotion({ retro_status: "待复盘", ui: { actions: { retro_write: ENABLED }, edits: [] } })]);
    expect(isDisabled(menuItem(await openMenu(), "写复盘"))).toBe(false);
    cleanup();
    renderPage([promotion({ retro_status: "已完成", ui: { actions: { retro_revise: ENABLED }, edits: ["cooperation"] } })]);
    expect(isDisabled(menuItem(await openMenu(), "修改复盘"))).toBe(false);
  });

  it("已完结单的旧写菜单禁用并给只读原因，管理员保留编辑能力", async () => {
    renderPage([promotion({ completed_at: "2026-10-20T10:00:00Z", ui: { actions: {}, edits: [] } })]);
    expect(isDisabled(menuItem(await openMenu(), "其他信息（已完结，只读）"))).toBe(true);
    cleanup();
    renderPage([promotion({ completed_at: "2026-10-20T10:00:00Z", ui: { actions: {}, edits: ["cooperation"] } })]);
    expect(isDisabled(menuItem(await openMenu(), "其他信息"))).toBe(false);
  });
});


describe("PR-7 复盘写入弹窗", () => {
  it("主管写完即完成，保存后刷新博主档案缓存", async () => {
    const qc=client();
    const refresh=vi.spyOn(qc,"invalidateQueries");
    const close=vi.fn();
    api.writeRetrospective.mockResolvedValue(promotion({retro_status:"已完成"}));
    render(<QueryClientProvider client={qc}><RetroModal target={promotion({retro_status:"待复盘"})} onClose={close}/></QueryClientProvider>);
    fireEvent.change(await screen.findByRole("textbox", {name:"复盘内容"}), {target:{value:"配合及时，建议二搭"}});
    fireEvent.click(screen.getByRole("button", {name:"完成复盘"}));
    await waitFor(()=>expect(api.writeRetrospective).toHaveBeenCalledWith("p1","配合及时，建议二搭"));
    await waitFor(()=>expect(close).toHaveBeenCalledOnce());
    expect(refresh).toHaveBeenCalledWith({queryKey:["blogger-retrospectives"]});
    expect(screen.queryByText("复盘已提交，等主管确认")).toBeNull();
  });

  it("管理员修改预填旧文并保存新版本，空白内容不发送", async () => {
    render(wrap(<RetroModal target={promotion({retro_status:"已完成",retro_content:"旧版内容"})} onClose={()=>{}}/>));
    const input=await screen.findByRole("textbox", {name:"复盘内容"});
    expect((input as HTMLTextAreaElement).value).toBe("旧版内容");
    fireEvent.change(input,{target:{value:"   "}});
    fireEvent.click(screen.getByRole("button",{name:"保存新版本"}));
    await screen.findByText("请填写复盘内容");
    expect(api.writeRetrospective).not.toHaveBeenCalled();
  });
});
