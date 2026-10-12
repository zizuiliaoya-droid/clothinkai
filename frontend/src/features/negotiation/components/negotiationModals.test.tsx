// @vitest-environment jsdom
// 谈款弹窗（流程线 8.3）：新建 / 编辑（套装成员行、缺项标红、退回意见、别人在谈）、主管审核、老板审核、收货、复制。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "@/stores/authStore";
import { SELF_REVIEW_NOTICE, SELF_REVIEW_REASON } from "../negotiationDialogs";
import type { Negotiation, NegotiationItem } from "../types";
import { CopyNegotiationModal } from "./CopyNegotiationModal";
import { FinalReviewModal } from "./FinalReviewModal";
import { ManagerReviewModal } from "./ManagerReviewModal";
import { NegotiationFormModal } from "./NegotiationFormModal";
import { NegotiationReceiverModal } from "./NegotiationReceiverModal";

const api = vi.hoisted(() => ({
  createNegotiation: vi.fn(),
  updateNegotiation: vi.fn(),
  submitNegotiation: vi.fn(),
  reviewNegotiation: vi.fn(),
  finalReviewNegotiation: vi.fn(),
  getNegotiation: vi.fn(),
  saveNegotiationReceiver: vi.fn(),
  confirmNegotiationReceiver: vi.fn(),
  copyNegotiation: vi.fn(),
  othersInProgress: vi.fn(),
  negotiationGoodsMembers: vi.fn(),
}));
vi.mock("@/features/negotiation/api", () => api);

const GOODS_CODE = "GM-SUIT-20261001";
const SKU_CODE = "SKU-7788-BLK-M";

vi.mock("@/features/product/api", () => ({
  listGoodsForStyle: () =>
    Promise.resolve([
      { goods_main_id: "g-1", goods_code: GOODS_CODE, goods_title: "飞狐套装全称", goods_short_name: "飞狐套装", is_suit: true },
    ]),
  listSkusByStyle: (styleId: string) =>
    Promise.resolve(
      styleId === "s-1"
        ? [{ id: "k-1", style_id: "s-1", sku_code: SKU_CODE, color: "黑色", size: "M" }]
        : [{ id: "k-2", style_id: styleId, sku_code: "SKU-9-WHT-S", color: "白色", size: "S" }]
    ),
}));
vi.mock("@/features/blogger/api", () => ({
  listBloggers: () => Promise.resolve({ items: [], total: 0, page: 1, page_size: 20 }),
}));

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
  for (const fn of Object.values(api)) fn.mockReset();
  api.othersInProgress.mockResolvedValue([]);
  api.negotiationGoodsMembers.mockResolvedValue({ goods_main_id: "g-1", members: SUIT_MEMBERS });
  useAuthStore.setState({ user: null });
});
afterEach(cleanup);

const SUIT_MEMBERS = [
  { style_id: "s-1", display_short_name: "飞狐短裙", goods_title: "飞狐短裙全称" },
  { style_id: "s-2", display_short_name: "飞狐上衣", goods_title: "飞狐上衣全称" },
];

function item(over: Partial<NegotiationItem> = {}): NegotiationItem {
  return {
    id: "i-1",
    style_id: "s-1",
    style_code: "ST-001",
    display_short_name: "飞狐短裙",
    goods_title: "飞狐短裙全称",
    sku_id: "k-1",
    color: "黑色",
    size: "M",
    style_main_image_url: null,
    ...over,
  };
}

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
    goods_title: "飞狐套装全称",
    goods_is_suit: true,
    pr_id: "u-pr",
    pr_name: "王小美",
    promotion_id: null,
    promotion_internal_code: null,
    cooperation_mode: "寄拍",
    platform: "小红书",
    scheduled_publish_date: "2026-12-20",
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
    display_short_name: "飞狐套装",
    items: [
      item(),
      item({ id: "i-2", style_id: "s-2", display_short_name: "飞狐上衣", goods_title: "飞狐上衣全称", sku_id: "k-2", color: "白色", size: "S" }),
    ],
    overdue: false,
    overdue_hours: null,
    receiver_suggestion: null,
    others_in_progress: null,
    ui: { actions: { edit: { state: "enabled" }, submit: { state: "enabled" } } },
    created_at: "2026-10-10T08:00:00+08:00",
    updated_at: "2026-10-10T08:00:00+08:00",
    ...over,
  };
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <MemoryRouter>
      <QueryClientProvider client={client}>{node}</QueryClientProvider>
    </MemoryRouter>
  );
}

/** antd 两个汉字的按钮会插空格（「保 存」）。 */
function button(name: string): HTMLElement {
  const re = new RegExp(`^${name.split("").join("\\s?")}$`);
  const all = screen.getAllByRole("button", { name: re });
  return all[all.length - 1];
}

function gateError(missing: { key: string; label: string }[]) {
  return { response: { status: 422, data: { code: "FLOW_GATE_MISSING", message: "缺项", details: { missing } } } };
}

describe("NegotiationFormModal", () => {
  it("套装：每个成员一行颜色尺码（选项是「颜色 / 尺码」）；别的 PR 在谈出黄色提示；不出商品编码与 SKU 编码", async () => {
    api.othersInProgress.mockResolvedValue([
      { pr_name: "李四", status: "主管待审", updated_at: "2026-10-06T10:00:00+08:00" },
    ]);
    render(wrap(<NegotiationFormModal target={{ editing: row() }} onClose={() => {}} />));
    expect(await screen.findByText("颜色尺码 · 飞狐短裙")).toBeTruthy();
    expect(screen.getByText("颜色尺码 · 飞狐上衣")).toBeTruthy();
    expect(await screen.findByText("黑色 / M")).toBeTruthy();
    expect(await screen.findByText("白色 / S")).toBeTruthy();
    expect(await screen.findByText("李四也在谈这个博主（主管待审，10-06 更新）")).toBeTruthy();
    await waitFor(() => expect(api.othersInProgress).toHaveBeenCalledWith("b-1", "n-1"));
    expect(document.body.textContent).not.toContain(GOODS_CODE);
    expect(document.body.textContent).not.toContain(SKU_CODE);
    // 草稿：保存草稿
    expect(button("保存草稿")).toBeTruthy();
  });

  it("保存并提交：前端先查缺项，缺的标红、顶部列出，不发请求", async () => {
    render(
      wrap(
        <NegotiationFormModal
          target={{
            editing: row({
              scheduled_publish_date: null,
              items: [item(), item({ id: "i-2", style_id: "s-2", display_short_name: "飞狐上衣", sku_id: null, color: null, size: null })],
            }),
          }}
          onClose={() => {}}
        />
      )
    );
    expect(await screen.findByText("颜色尺码 · 飞狐上衣")).toBeTruthy();
    fireEvent.click(button("保存并提交"));
    expect(await screen.findByText("还缺：颜色尺码、约定发布时间")).toBeTruthy();
    await waitFor(() => expect(screen.getAllByText("必填")).toHaveLength(2));
    expect(api.updateNegotiation).not.toHaveBeenCalled();
    expect(api.submitNegotiation).not.toHaveBeenCalled();
  });

  it("保存并提交：先存（明细按成员顺序）再提交；后端 422 的缺项标回，弹窗不关", async () => {
    const onClose = vi.fn();
    api.updateNegotiation.mockResolvedValue(row());
    api.submitNegotiation.mockRejectedValue(gateError([{ key: "goods_items", label: "颜色尺码" }]));
    render(wrap(<NegotiationFormModal target={{ editing: row() }} onClose={onClose} />));
    expect(await screen.findByText("白色 / S")).toBeTruthy();
    fireEvent.click(button("保存并提交"));
    await waitFor(() => expect(api.submitNegotiation).toHaveBeenCalledWith("n-1"));
    expect(api.updateNegotiation).toHaveBeenCalledWith(
      "n-1",
      expect.objectContaining({
        goods_main_id: "g-1",
        scheduled_publish_date: "2026-12-20",
        quote_amount: "300",
        items: [
          { style_id: "s-1", sku_id: "k-1" },
          { style_id: "s-2", sku_id: "k-2" },
        ],
      })
    );
    expect(await screen.findByText("还缺：颜色尺码")).toBeTruthy();
    // 每行都选了还报缺（sku 已删 / 成员变了）：两行都标
    await waitFor(() => expect(screen.getAllByText("必填")).toHaveLength(2));
    expect(onClose).not.toHaveBeenCalled();
  });

  it("主管自建的谈款提交成功：提示找管理员临时授权代理人（11-24）", async () => {
    const onClose = vi.fn();
    api.updateNegotiation.mockResolvedValue(row());
    api.submitNegotiation.mockResolvedValue(
      row({
        status: "主管待审",
        ui: { actions: { manager_review: { state: "disabled", reason: SELF_REVIEW_REASON } } },
      })
    );
    render(wrap(<NegotiationFormModal target={{ editing: row() }} onClose={onClose} />));
    expect(await screen.findByText("白色 / S")).toBeTruthy();
    fireEvent.click(button("保存并提交"));
    expect(await screen.findByText(SELF_REVIEW_NOTICE)).toBeTruthy();
    expect(onClose).toHaveBeenCalled();
  });

  it("老板退回：顶部只读显示退回意见；按钮是「保存」；菜单带来的缺项打开即标红", async () => {
    render(
      wrap(
        <NegotiationFormModal
          target={{
            editing: row({
              status: "老板退回",
              final_reviewer_name: "老板",
              final_review_opinion: "服务费太高，压到 200",
            }),
            missing: [{ key: "scheduled_publish_date", label: "约定发布时间" }],
          }}
          onClose={() => {}}
        />
      )
    );
    expect(await screen.findByText("老板退回意见（老板）")).toBeTruthy();
    expect(screen.getByText("服务费太高，压到 200")).toBeTruthy();
    expect(screen.getByText("还缺：约定发布时间")).toBeTruthy();
    expect(button("保存")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /保\s?存\s?草\s?稿/ })).toBeNull();
  });
});

describe("ManagerReviewModal / FinalReviewModal", () => {
  it("主管驳回：提示驳回后结束；没写意见不发请求；写了带意见驳回", async () => {
    api.reviewNegotiation.mockResolvedValue(row({ status: "审核驳回" }));
    const onClose = vi.fn();
    render(
      wrap(
        <ManagerReviewModal
          target={{ row: row({ status: "主管待审" }), action: { state: "enabled" } }}
          onClose={onClose}
        />
      )
    );
    expect(await screen.findByText("飞狐短裙 · 黑色 / M")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("驳回"));
    expect(await screen.findByText(/驳回后这张谈款结束/)).toBeTruthy();
    fireEvent.click(button("确认驳回"));
    expect(await screen.findByText("驳回要写意见")).toBeTruthy();
    expect(api.reviewNegotiation).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("驳回意见"), { target: { value: " 报价太高 " } });
    fireEvent.click(button("确认驳回"));
    await waitFor(() =>
      expect(api.reviewNegotiation).toHaveBeenCalledWith("n-1", { action: "reject", review_opinion: "报价太高" })
    );
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("主管通过：意见可选", async () => {
    api.reviewNegotiation.mockResolvedValue(row({ status: "待老板审核" }));
    render(
      wrap(
        <ManagerReviewModal target={{ row: row({ status: "主管待审" }), action: { state: "enabled" } }} onClose={() => {}} />
      )
    );
    fireEvent.click(await screen.findByRole("button", { name: /确\s?认\s?通\s?过/ }));
    await waitFor(() =>
      expect(api.reviewNegotiation).toHaveBeenCalledWith("n-1", { action: "approve", review_opinion: null })
    );
  });

  it("老板退回要写意见；显示主管意见", async () => {
    api.finalReviewNegotiation.mockResolvedValue(row({ status: "老板退回" }));
    render(
      wrap(
        <FinalReviewModal
          target={{
            row: row({ status: "待老板审核", reviewer_name: "张主管", review_opinion: "可以" }),
            action: { state: "enabled" },
          }}
          onClose={() => {}}
        />
      )
    );
    expect(await screen.findByText("主管意见（张主管）")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("退回"));
    expect(await screen.findByText("退回后 PR 改完重提，仍先过主管")).toBeTruthy();
    fireEvent.click(button("确认退回"));
    expect(await screen.findByText("退回要写意见")).toBeTruthy();
    expect(api.finalReviewNegotiation).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("退回意见"), { target: { value: "压价" } });
    fireEvent.click(button("确认退回"));
    await waitFor(() =>
      expect(api.finalReviewNegotiation).toHaveBeenCalledWith("n-1", { action: "return", opinion: "压价" })
    );
  });
});

describe("NegotiationReceiverModal", () => {
  const receiverUi = {
    actions: { receiver_save: { state: "enabled" as const }, receiver_confirm: { state: "enabled" as const } },
    fields: { receiver: { state: "edit" as const } },
  };

  it("主管代录：顶部写谈款 PR；带出建议并说明；二次确认后生成推广单，可去推广管理", async () => {
    useAuthStore.setState({
      user: { id: "u-mgr", username: "mgr", display_name: "张主管", email: null, status: "active", roles: ["pr_manager"] },
    });
    const pending = row({ status: "待补收货地址", ui: receiverUi });
    api.getNegotiation.mockResolvedValue({
      ...pending,
      receiver_suggestion: {
        receiver_name: "张三",
        receiver_phone: "13800000000",
        receiver_address: "上海市徐汇区",
        source: "promotion",
        source_label: "推广单 DE2610030A1 · 10-03",
      },
    });
    api.confirmNegotiationReceiver.mockResolvedValue(
      row({ status: "已定稿", promotion_id: "p-1", promotion_internal_code: "DE2610100B2" })
    );
    render(wrap(<NegotiationReceiverModal target={{ row: pending, action: { state: "enabled" } }} onClose={() => {}} />));
    expect(await screen.findByText("谈款 PR：王小美（由你代为确认）")).toBeTruthy();
    expect(await screen.findByText("已带出 推广单 DE2610030A1 · 10-03 的收货信息，请核对")).toBeTruthy();
    expect(screen.getByDisplayValue("张三")).toBeTruthy();

    fireEvent.click(button("确认并生成推广单"));
    expect(await screen.findByText("确认后生成推广单，之后改地址请到推广单上改")).toBeTruthy();
    expect(api.confirmNegotiationReceiver).not.toHaveBeenCalled();
    fireEvent.click(button("确认并生成推广单"));
    await waitFor(() =>
      expect(api.confirmNegotiationReceiver).toHaveBeenCalledWith("n-1", {
        receiver_name: "张三",
        receiver_phone: "13800000000",
        receiver_address: "上海市徐汇区",
      })
    );
    expect(await screen.findByText("已生成推广单 DE2610100B2", { selector: ".ant-modal-confirm-title" })).toBeTruthy();
    expect(button("去推广管理")).toBeTruthy();
  });

  it("本人：不写代确认；三项没填全先标红，不弹二次确认；只有保存权限时不出确认按钮", async () => {
    useAuthStore.setState({
      user: { id: "u-pr", username: "pr", display_name: "王小美", email: null, status: "active", roles: ["pr"] },
    });
    const pending = row({ status: "待补收货地址", receiver_name: "李四", ui: receiverUi });
    api.getNegotiation.mockResolvedValue(pending);
    const view = render(
      wrap(<NegotiationReceiverModal target={{ row: pending, action: { state: "enabled" } }} onClose={() => {}} />)
    );
    expect(await screen.findByDisplayValue("李四")).toBeTruthy();
    expect(screen.queryByText(/由你代为确认/)).toBeNull();
    fireEvent.click(button("确认并生成推广单"));
    await waitFor(() => expect(screen.getAllByText("必填")).toHaveLength(2));
    expect(screen.queryByText("确认后生成推广单，之后改地址请到推广单上改")).toBeNull();
    view.unmount();

    const saveOnly = row({
      id: "n-2",
      status: "待补收货地址",
      ui: { actions: { receiver_save: { state: "enabled" } }, fields: { receiver: { state: "edit" } } },
    });
    api.getNegotiation.mockResolvedValue(saveOnly);
    render(wrap(<NegotiationReceiverModal target={{ row: saveOnly, action: { state: "enabled" } }} onClose={() => {}} />));
    expect(await screen.findByRole("button", { name: /保\s?存/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "确认并生成推广单" })).toBeNull();
  });
});

describe("CopyNegotiationModal", () => {
  it("确认框写明复制哪些；复制成功交出新草稿", async () => {
    const created = row({ id: "n-new", copied_from_id: "n-1" });
    api.copyNegotiation.mockResolvedValue(created);
    const onCopied = vi.fn();
    render(
      wrap(
        <CopyNegotiationModal
          target={{ row: row({ status: "审核驳回", review_opinion: "报价太高" }), action: { state: "enabled" } }}
          onClose={() => {}}
          onCopied={onCopied}
        />
      )
    );
    expect(await screen.findByText(/复制博主、商品、颜色尺码、模式、平台、服务费、备注和约定发布时间/)).toBeTruthy();
    expect(screen.getByText("驳回意见：报价太高")).toBeTruthy();
    fireEvent.click(button("复制"));
    await waitFor(() => expect(onCopied).toHaveBeenCalledWith(created));
    expect(api.copyNegotiation).toHaveBeenCalledWith("n-1");
  });
});
