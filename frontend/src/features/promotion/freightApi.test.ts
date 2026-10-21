// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/services/apiClient";
import { getFreightPayment, listFreightPayments, payFreightPayment, rejectFreightPayment, resubmitFreightPayment, reviewFreightPayment, submitFreightPayment } from "./freightApi";
import { recallFailurePromotion, recallSuccessPromotion, uploadRecallWaybillFile } from "./api";

vi.mock("@/services/apiClient", () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }));
beforeEach(() => { vi.mocked(apiClient.get).mockResolvedValue({ data: { id: "f1" } }); vi.mocked(apiClient.post).mockResolvedValue({ data: { id: "f1" } }); });

describe("R33 七端点合同", () => {
  it("召回成功带费用承担方、失败带备注，寄回图后端代传", async () => {
    await recallSuccessPromotion("p1", { return_shipping_fee: "0", return_shipping_payer: "到付" });
    expect(apiClient.post).toHaveBeenCalledWith("/api/promotions/p1/recall/success", { return_shipping_fee: "0", return_shipping_payer: "到付" });
    await recallFailurePromotion("p1", { remark: "合成失败原因" });
    expect(apiClient.post).toHaveBeenCalledWith("/api/promotions/p1/recall/failure", { remark: "合成失败原因" });
    await uploadRecallWaybillFile("p1", new File(["synthetic"], "waybill.png", { type: "image/png" }));
    expect(apiClient.post).toHaveBeenLastCalledWith("/api/promotions/p1/recall/waybill", expect.any(FormData), { timeout: 120_000 });
  });
  it("列表完整透传筛选与分页；详情独立GET", async () => {
    const filters = { status: "已付款" as const, keyword: "测试", payment_date_from: "2026-10-01", payment_date_to: "2026-10-21", promotion_id: "p1", page: 2, page_size: 20 };
    await listFreightPayments(filters);
    expect(apiClient.get).toHaveBeenCalledWith("/api/return-freight-payments/", { params: filters });
    expect(await getFreightPayment("f1")).toEqual({ id: "f1" });
    expect(apiClient.get).toHaveBeenCalledWith("/api/return-freight-payments/f1");
  });
  it("金额保持字符串，审核/驳回/重提字段精确", async () => {
    await submitFreightPayment("p1", "18.30");
    expect(apiClient.post).toHaveBeenCalledWith("/api/promotions/p1/return-freight", { amount: "18.30" });
    await reviewFreightPayment("f1", { action: "reject", reason: "需核对" });
    expect(apiClient.post).toHaveBeenCalledWith("/api/return-freight-payments/f1/review", { action: "reject", reason: "需核对" });
    await rejectFreightPayment("f1", "码不清晰");
    expect(apiClient.post).toHaveBeenCalledWith("/api/return-freight-payments/f1/reject-payment", { reason: "码不清晰" });
    await resubmitFreightPayment("f1", { note: "已更新", amount: "19.00" });
    expect(apiClient.post).toHaveBeenCalledWith("/api/return-freight-payments/f1/resubmit", { note: "已更新", amount: "19.00" });
  });
  it("付款只传multipart file、不传客户端日期", async () => {
    const file = new File(["synthetic"], "proof.png", { type: "image/png" });
    await payFreightPayment("f1", file);
    const [url, body, config] = vi.mocked(apiClient.post).mock.calls.at(-1)!;
    expect(url).toBe("/api/return-freight-payments/f1/pay");
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get("file")).toMatchObject({ name: file.name, size: file.size, type: file.type });
    expect([...(body as FormData).keys()]).toEqual(["file"]);
    expect(config).toEqual({ timeout: 120_000 });
  });
});
