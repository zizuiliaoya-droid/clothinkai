// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { Promotion } from "../types";
import { RecallModal } from "./RecallModal";
import { RecallResultModal } from "./RecallResultModal";
import { RecallWaybillModal } from "./RecallWaybillModal";
import { FreightModal } from "./FreightModal";

const api = vi.hoisted(() => ({ getPromotion: vi.fn(), startRecallPromotion: vi.fn(), recallSuccessPromotion: vi.fn(), recallFailurePromotion: vi.fn(), uploadRecallWaybillFile: vi.fn(), uploadPaymentQrFile: vi.fn() }));
const freightApi = vi.hoisted(() => ({ getFreightPayment: vi.fn(), resubmitFreightPayment: vi.fn(), reviewFreightPayment: vi.fn() }));
vi.mock("../api", async (original) => ({ ...await original<typeof import("../api")>(), ...api }));
vi.mock("../freightApi", async (original) => ({ ...await original<typeof import("../freightApi")>(), ...freightApi }));
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window); window.getComputedStyle = (el) => getStyle(el);
  Object.defineProperty(window, "matchMedia", { writable: true, value: (query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }) });
});
afterEach(cleanup);
beforeEach(() => [...Object.values(api), ...Object.values(freightApi)].forEach((f) => f.mockReset()));
const row = (over: Partial<Promotion> = {}) => ({ id: "p1", internal_code: "SYN-P001", line_id: "L1", publish_status: "未发布", recall_status: "未召回", is_active: true, ui: { actions: { recall_start: { state: "enabled" } } }, ...over } as Promotion);
function mount(promotion = row(), Component = RecallModal) {
  api.getPromotion.mockResolvedValue(promotion);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><Component target={promotion} onClose={() => {}} /></QueryClientProvider>);
}
describe("召回发起", () => {
  it("未发布提示同时取消，原因必填且trim后提交", async () => {
    mount();
    expect(await screen.findByText("这张单还没发布，召回会同时取消推广")).toBeTruthy();
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "发起召回" }));
    expect(await screen.findByText("请填写召回原因")).toBeTruthy();
    expect(api.startRecallPromotion).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("召回原因"), { target: { value: "  尺码不合适  " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "发起召回" }));
    await waitFor(() => expect(api.startRecallPromotion).toHaveBeenCalledWith("p1", { recall_reason: "尺码不合适" }));
  });
  it("详情没有能力时不能凭旧行状态发起", async () => {
    mount(row({ ui: { actions: {} } }));
    expect((await screen.findByRole("button", { name: "发起召回" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("409后保留原因、刷新能力且不重发", async () => {
    mount();
    api.startRecallPromotion.mockRejectedValue({ response: { status: 409, data: {} } });
    await screen.findByText("这张单还没发布，召回会同时取消推广");
    fireEvent.change(await screen.findByLabelText("召回原因"), { target: { value: "保留的原因" } });
    fireEvent.click(screen.getByRole("button", { name: "发起召回" }));
    expect(await screen.findByText("这张单刚被别人处理过，已刷新")).toBeTruthy();
    expect((screen.getByLabelText("召回原因") as HTMLInputElement).value).toBe("保留的原因");
    expect(api.startRecallPromotion).toHaveBeenCalledTimes(1);
  });
});

describe("推广侧R33", () => {
  function mountFreight(mode: "review" | "resubmit", amount: string | null = "30.00") {
    const promotion = row({ return_shipping_fee: "30.00", payment_qr_attachment_id: "current-qr", recall_waybill_attachment_id: "current-waybill", ui: { actions: { recall_waybill: { state: "enabled" } }, fields: { payment_qr: { state: "edit" } } }, freight_payment: { id: "f1", status: mode === "review" ? "待审核" : "已驳回", amount: "30.00", reject_reason: "旧码模糊", payment_date: null, ui: { actions: {} } } });
    api.getPromotion.mockResolvedValue(promotion);
    freightApi.getFreightPayment.mockResolvedValue({ id: "f1", amount, status: promotion.freight_payment!.status, payment_qr_signed_url: "https://synthetic.invalid/old.png", waybill_signed_url: null, payment_proof_signed_url: null, submitted_at: "2026-10-21T00:00:00Z", reject_reason: "旧码模糊", ui: { actions: { [mode === "review" ? "freight_review" : "freight_resubmit"]: { state: "enabled" } } } });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={client}><FreightModal target={promotion} mode={mode} onClose={() => {}} /></QueryClientProvider>);
  }
  it("R33金额被裁剪时不以推广当前运费回填", async () => {
    mountFreight("resubmit", null);
    expect((await screen.findByLabelText("运费金额") as HTMLInputElement).value).toBe("");
  });
  it("重提选择了新收款码时必须先上传，避免重提旧快照", async () => {
    mountFreight("resubmit");
    await screen.findByLabelText("运费金额");
    const dialog = screen.getByRole("dialog");
    fireEvent.change(dialog.querySelector('input[type="file"]')!, { target: { files: [new File(["synthetic"], "new-qr.png", { type: "image/png" })] } });
    const submit = screen.getByRole("button", { name: "重提寄回运费" }) as HTMLButtonElement;
    await waitFor(() => expect(submit.disabled).toBe(true));
    fireEvent.change(screen.getByLabelText("重提说明"), { target: { value: "已替换收款码" } });
    fireEvent.click(submit);
    expect(freightApi.resubmitFreightPayment).not.toHaveBeenCalled();
    api.uploadPaymentQrFile.mockResolvedValue(row());
    fireEvent.click(screen.getByRole("button", { name: /替换收款码/ }));
    await waitFor(() => expect(api.uploadPaymentQrFile).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(submit.disabled).toBe(false));
    fireEvent.click(submit);
    await waitFor(() => expect(freightApi.resubmitFreightPayment).toHaveBeenCalledTimes(1));
    expect(api.uploadPaymentQrFile.mock.invocationCallOrder[0]).toBeLessThan(freightApi.resubmitFreightPayment.mock.invocationCallOrder[0]);
  });
  it("主管读取快照、驳回理由必填后传审核合同", async () => {
    mountFreight("review");
    expect((await screen.findByAltText("收款码快照")).getAttribute("src")).toContain("old.png");
    fireEvent.click(screen.getByLabelText("驳回"));
    fireEvent.click(screen.getByRole("button", { name: "确认审核" }));
    expect(await screen.findByText("请填写驳回原因")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("驳回原因"), { target: { value: "  重新核对运费  " } });
    fireEvent.click(screen.getByRole("button", { name: "确认审核" }));
    await waitFor(() => expect(freightApi.reviewFreightPayment).toHaveBeenCalledWith("f1", { action: "reject", reason: "重新核对运费" }));
  });
  it("重提说明必填、金额取当前单、不展示承担方编辑", async () => {
    mountFreight("resubmit");
    const submit = await screen.findByRole("button", { name: "重提寄回运费" });
    await screen.findByLabelText("运费金额");
    expect(screen.queryByLabelText("承担方")).toBeNull();
    fireEvent.click(submit);
    expect(await screen.findByText("请填写重提说明")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("重提说明"), { target: { value: "已补新截图" } });
    fireEvent.click(submit);
    await waitFor(() => expect(freightApi.resubmitFreightPayment).toHaveBeenCalledWith("f1", { amount: "30.00", note: "已补新截图" }));
  });
});

describe("召回结果与截图", () => {
  const recalling = (over: Partial<Promotion> = {}) => row({ recall_status: "召回中", recall_waybill_attachment_id: "waybill1", payment_qr_attachment_id: null, return_shipping_fee: null, return_shipping_payer: null, ui: { actions: { recall_result: { state: "enabled" }, recall_waybill: { state: "enabled" } }, fields: { payment_qr: { state: "edit" }, recall_info: { state: "edit" } } }, ...over });
  it("空金额不当0；0运费无需收款码，保留完结单专用动作", async () => {
    mount(recalling({ completed_at: "2026-10-21T01:00:00Z", publish_status: "已发布" }), RecallResultModal);
    const submit = await screen.findByRole("button", { name: "登记召回结果" });
    fireEvent.click(submit);
    expect(await screen.findByText("请填写运费，免费请填 0")).toBeTruthy();
    expect(api.recallSuccessPromotion).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("寄回运费"), { target: { value: "0" } });
    fireEvent.click(submit);
    await waitFor(() => expect(api.recallSuccessPromotion).toHaveBeenCalledWith("p1", expect.objectContaining({ return_shipping_fee: "0", return_shipping_payer: "博主垫付" })));
  });
  it("垫付正金额缺码显示上传入口且不能提交；到付无需码", async () => {
    mount(recalling(), RecallResultModal);
    fireEvent.change(await screen.findByLabelText("寄回运费"), { target: { value: "30" } });
    expect(await screen.findByText("会生成寄回运费付款单，需要博主收款码")).toBeTruthy();
    expect(screen.getByRole("button", { name: /上传收款码/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "登记召回结果" }));
    expect(api.recallSuccessPromotion).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("到付"));
    fireEvent.click(screen.getByRole("button", { name: "登记召回结果" }));
    await waitFor(() => expect(api.recallSuccessPromotion).toHaveBeenCalledWith("p1", expect.objectContaining({ return_shipping_fee: "30", return_shipping_payer: "到付" })));
  });
  it("成功始终要求寄回图；失败将备注带给后端", async () => {
    mount(recalling({ recall_waybill_attachment_id: null }), RecallResultModal);
    fireEvent.change(await screen.findByLabelText("寄回运费"), { target: { value: "0" } });
    fireEvent.click(screen.getByRole("button", { name: "登记召回结果" }));
    expect(api.recallSuccessPromotion).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("召回失败"));
    fireEvent.change(screen.getByLabelText("结果备注"), { target: { value: "博主遗失" } });
    fireEvent.click(screen.getByRole("button", { name: "登记召回结果" }));
    await waitFor(() => expect(api.recallFailurePromotion).toHaveBeenCalledWith("p1", { remark: "博主遗失" }));
  });
  it("寄回图提示只截单号与运费，能力缺失不展示上传", async () => {
    mount(recalling(), RecallWaybillModal);
    expect(await screen.findByText("只截单号与运费部分")).toBeTruthy();
    expect(screen.getByText("已上传寄回截图（暂时无法预览）")).toBeTruthy();
  });
});
