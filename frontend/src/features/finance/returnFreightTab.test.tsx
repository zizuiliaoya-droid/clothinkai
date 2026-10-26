// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReturnFreightPayment } from "@/features/promotion/freightTypes";
import { ReturnFreightTab } from "./ReturnFreightTab";

const api = vi.hoisted(() => ({ listFreightPayments: vi.fn(), getFreightPayment: vi.fn(), payFreightPayment: vi.fn(), rejectFreightPayment: vi.fn() }));
vi.mock("@/features/promotion/freightApi", async (original) => ({ ...await original<typeof import("@/features/promotion/freightApi")>(), ...api }));
vi.mock("@/features/flow/LineDrawer", () => ({ LineDrawer: ({ lineId }: { lineId: string | null }) => lineId ? <div>时间线 {lineId}</div> : null }));
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (el) => getStyle(el);
  Object.defineProperty(window, "matchMedia", { writable: true, value: (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }) });
});
afterEach(cleanup);
beforeEach(() => { Object.values(api).forEach((f) => f.mockReset()); });
const row = (over: Partial<ReturnFreightPayment> = {}) => ({
  id: "f1", promotion_id: "p1", line_id: "L1", promotion_internal_code: "SYN-P001", blogger_nickname: "合成博主", display_short_name: "短裙", amount: "30.00", status: "待付款", submitted_by_name: "合成PR", submitted_at: "2026-10-21T01:00:00Z", reviewed_by_name: "合成主管", payment_qr_attachment_id: "qr-old", payment_qr_signed_url: "https://synthetic.invalid/qr-snapshot.png", waybill_attachment_id: "waybill-old", waybill_signed_url: "https://synthetic.invalid/waybill-snapshot.png", payment_proof_signed_url: null, ui: { actions: {} }, ...over,
} as ReturnFreightPayment);
function mount(r = row()) {
  api.listFreightPayments.mockResolvedValue({ items: [r], total: 21, page: 1, page_size: 20 });
  api.getFreightPayment.mockResolvedValue(r);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><ReturnFreightTab /></QueryClientProvider>);
  return client;
}

describe("寄回运费页签", () => {
  it("主管财务页只读，显示快照并可开时间线", async () => {
    mount(row({ ui: { actions: { freight_review: { state: "enabled" } } } }));
    expect(await screen.findByText("SYN-P001")).toBeTruthy();
    expect(screen.getByText("¥30.00")).toBeTruthy();
    expect(screen.getByAltText("收款码快照").getAttribute("src")).toContain("qr-snapshot");
    expect(screen.queryByRole("button", { name: /审\s*核/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /付\s*款/ })).toBeNull();
    fireEvent.click(screen.getByText("SYN-P001"));
    expect(await screen.findByText("时间线 L1")).toBeTruthy();
  });
  it("裁剪的金额和快照不显示", async () => {
    mount(row({ amount: null, payment_qr_attachment_id: null, payment_qr_signed_url: null, waybill_attachment_id: null, waybill_signed_url: null }));
    await screen.findByText("SYN-P001");
    expect(screen.queryByText("金额")).toBeNull();
    expect(screen.queryByAltText("收款码快照")).toBeNull();
    expect(screen.queryByAltText("寄回截图快照")).toBeNull();
  });
  it("关键词和付款日期筛选发送交集并回第一页", async () => {
    mount();
    await screen.findByText("SYN-P001");
    fireEvent.change(screen.getByLabelText("运费关键词"), { target: { value: "短裙" } });
    fireEvent.change(screen.getByLabelText("付款开始日期"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("付款结束日期"), { target: { value: "2026-10-21" } });
    await waitFor(() => expect(api.listFreightPayments).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: "短裙", payment_date_from: "2026-10-01", payment_date_to: "2026-10-21", page: 1 })));
  });
  it("财务付款需凭证，409保留文件和弹窗、不自动重发", async () => {
    mount(row({ ui: { actions: { freight_pay: { state: "enabled" } } } }));
    api.payFreightPayment.mockRejectedValue({ response: { status: 409, data: { code: "FREIGHT_STATE_CONFLICT" } } });
    fireEvent.click(await screen.findByRole("button", { name: /^付\s*款$/ }));
    const dialog = await screen.findByRole("dialog");
    const submit = await within(dialog).findByRole("button", { name: "确认付款" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    expect(within(dialog).getByText(/系统自动记录（北京时间）/)).toBeTruthy();
    const file = new File(["synthetic"], "合成凭证.png", { type: "image/png" });
    fireEvent.change(dialog.querySelector('input[type="file"]')!, { target: { files: [file] } });
    await within(dialog).findByText(/合成凭证.png/);
    fireEvent.click(submit);
    await waitFor(() => expect(api.payFreightPayment).toHaveBeenCalledWith("f1", file));
    expect(await screen.findByText("这张单刚被别人处理过，已刷新")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(within(dialog).getByText(/合成凭证.png/)).toBeTruthy();
    expect(api.payFreightPayment).toHaveBeenCalledTimes(1);
  });
  it("财务驳回原因必填，成功刷新关联查询", async () => {
    const client = mount(row({ ui: { actions: { freight_reject_payment: { state: "enabled" } } } }));
    api.rejectFreightPayment.mockResolvedValue(row({ status: "已驳回" }));
    const invalidate = vi.spyOn(client, "invalidateQueries");
    fireEvent.click(await screen.findByRole("button", { name: /^驳\s*回$/ }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(await within(dialog).findByRole("button", { name: "确认驳回" }));
    expect(await within(dialog).findByText("请填写驳回原因")).toBeTruthy();
    expect(api.rejectFreightPayment).not.toHaveBeenCalled();
    fireEvent.change(within(dialog).getByLabelText("驳回原因"), { target: { value: "  换清晰截图  " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "确认驳回" }));
    await waitFor(() => expect(api.rejectFreightPayment).toHaveBeenCalledWith("f1", "换清晰截图"));
    for (const key of [["promotions"], ["promotion-stage-counts"], ["line-events", "L1"], ["return-freight-payments"], ["settlements"]]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey: key });
    }
  });
});
