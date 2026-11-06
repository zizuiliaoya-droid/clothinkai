// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, beforeAll, beforeEach, expect, it, vi} from "vitest";
import {Modal} from "antd";
import type {Negotiation} from "../types";
import type {BulkAction, BulkResult} from "../bulkApi";
import {BulkActionModal} from "./BulkActionModal";

const api = vi.hoisted(() => ({getNegotiation: vi.fn(), bulkAction: vi.fn()}));
vi.mock("../api", () => ({getNegotiation: api.getNegotiation}));
vi.mock("../bulkApi", async original => ({...await original<typeof import("../bulkApi")>(), bulkAction: api.bulkAction}));
vi.mock("./useNegotiationFeedback", () => ({useNegotiationFeedback: () => ({refresh: vi.fn()})}));
vi.mock("./NegotiationSummary", () => ({NegotiationSummary: ({row}: {row: Negotiation}) => <span>单据 {row.id}</span>}));
const access = vi.hoisted(() => ({readonly: false}));
vi.mock("@/stores/supportStore", () => ({useReadOnlySupport: () => access.readonly}));
vi.mock("@/stores/authStore", () => ({useAuthStore: () => "owner"}));
beforeAll(() => {const original = window.getComputedStyle.bind(window); window.getComputedStyle = element => original(element); Object.defineProperty(window, "matchMedia", {value: () => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}})});});
beforeEach(() => {vi.clearAllMocks(); access.readonly = false;});
afterEach(() => {cleanup(); Modal.destroyAll();});
function row(id: string, over: Partial<Negotiation> = {}): Negotiation {
  return {id, blogger_nickname: "博主", style_name: id, status: "草稿", receiver_name: "收件人", receiver_phone: "13800000000", receiver_address: "示例地址", pr_id: "owner", ui: {actions: {submit: {state: "enabled"}, manager_review: {state: "enabled"}, final_review: {state: "enabled"}, receiver_confirm: {state: "enabled"}}}, ...over} as Negotiation;
}
function result(id: string, status: BulkResult["status"] = "success"): BulkResult {
  return {row_key: id, negotiation_id: status === "success" ? id : null, status, code: status, message: `${id}${status === "success" ? "已处理" : "字段不合格"}`, reused: false, negotiation: null};
}
async function show(rows: Negotiation[], action: BulkAction = "submit") {
  api.getNegotiation.mockImplementation((id: string) => Promise.resolve(rows.find(item => item.id === id)));
  render(<BulkActionModal target={{rows, action}} onClose={() => {}} />);
  await waitFor(() => expect(screen.getByRole("button", {name: "执行本阶段未成功项"})).not.toHaveClass("ant-btn-loading"));
}
it("混合权限与缺项逐行未执行，仅提交可执行行，不把200视为整批成功", async () => {
  await show([row("a"), row("b", {ui: {actions: {submit: {state: "enabled", missing: [{key: "goods_items", label: "颜色尺码"}]}}}}), row("c", {ui: {actions: {}}})]);
  api.bulkAction.mockResolvedValue({results: [result("a")]});
  fireEvent.click(screen.getByRole("button", {name: "执行本阶段未成功项"}));
  await waitFor(() => expect(api.bulkAction).toHaveBeenCalledWith("submit", [{negotiation_id: "a", payload: {}}]));
  expect(await screen.findByText("成功：a已处理")).toBeInTheDocument();
  expect(screen.getAllByText(/请先编辑补齐：颜色尺码/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/当前没有此操作权限/).length).toBeGreaterThan(0);
});
it("部分失败后只重试失败ID，成功ID和结果保留", async () => {
  await show([row("a"), row("b")]);
  api.bulkAction.mockResolvedValueOnce({results: [result("a"), result("b", "failed")]}).mockResolvedValueOnce({results: [result("b")]});
  fireEvent.click(screen.getByRole("button", {name: "执行本阶段未成功项"}));
  await screen.findByText("失败：b字段不合格");
  fireEvent.click(screen.getByRole("button", {name: "执行本阶段未成功项"}));
  await waitFor(() => expect(api.bulkAction).toHaveBeenLastCalledWith("submit", [{negotiation_id: "b", payload: {}}]));
  expect(await screen.findByText("成功：b已处理")).toBeInTheDocument();
  expect(screen.getByText("单据 ID：a")).toBeInTheDocument();
});
it("响应丢失先查已知ID，状态变化记未执行而不是自己的成功", async () => {
  await show([row("a")]);
  api.bulkAction.mockRejectedValue(new Error("连接中断"));
  fireEvent.click(screen.getByRole("button", {name: "执行本阶段未成功项"}));
  await screen.findByText(/结果未收到，请先核对再重试/);
  api.getNegotiation.mockResolvedValue(row("a", {status: "主管待审"}));
  fireEvent.click(screen.getByRole("button", {name: "核对并重试待处理项"}));
  expect(await screen.findByText("未执行：状态已变化，不计为本次成功")).toBeInTheDocument();
  expect(api.bulkAction).toHaveBeenCalledTimes(1);
  expect(api.getNegotiation).toHaveBeenCalledTimes(2);
});
it("主管驳回逐行意见必填，补齐后仍只调用主管阶段", async () => {
  await show([row("a", {status: "主管待审"})], "review");
  fireEvent.mouseDown(screen.getByRole("combobox", {name: "a审核结果"}));
  fireEvent.click(screen.getByText("驳回，结束此单"));
  fireEvent.click(screen.getByRole("button", {name: "执行本阶段未成功项"}));
  expect(await screen.findByText("未执行：驳回或退回必须填写意见")).toBeInTheDocument();
  expect(api.bulkAction).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("a审核意见"), {target: {value: " 不适合 "}});
  api.bulkAction.mockResolvedValue({results: [result("a")]});
  fireEvent.click(screen.getByRole("button", {name: "执行本阶段未成功项"}));
  await waitFor(() => expect(api.bulkAction).toHaveBeenCalledWith("review", [{negotiation_id: "a", payload: {action: "reject", review_opinion: "不适合"}}]));
});
it("提交后行级INTERNAL_ERROR先GET核对，审核已变阶段不算本次成功", async () => {
  await show([row("a", {status: "主管待审"})], "review");
  api.bulkAction.mockResolvedValue({results: [{...result("a", "failed"), code: "INTERNAL_ERROR"}]});
  fireEvent.click(screen.getByRole("button", {name: "执行本阶段未成功项"}));
  await screen.findByText("待核对：处理结果尚未确认，请先核对再重试");
  expect(screen.getByLabelText("a审核意见")).toBeDisabled();
  expect(screen.getByText(/成功 0，未执行 0，失败 0，待核对 1/)).toBeInTheDocument();
  api.getNegotiation.mockResolvedValue(row("a", {status: "待老板审核"}));
  const retry = await waitFor(() => {
    const button = screen.getByRole("button", {name: "核对并重试待处理项"});
    expect(button).not.toHaveClass("ant-btn-loading");
    expect(button).toBeEnabled();
    return button;
  });
  fireEvent.click(retry);
  expect(await screen.findByText("未执行：状态已变化，不计为本次成功")).toBeInTheDocument();
  expect(api.getNegotiation).toHaveBeenCalledTimes(2);
  expect(api.getNegotiation).toHaveBeenLastCalledWith("a");
  expect(api.bulkAction).toHaveBeenCalledTimes(1);
});
it("收货地址各自预填且必须二次确认，缺地址行不提交", async () => {
  await show([row("a", {status: "待补收货地址", receiver_name: " 甲 "}), row("b", {status: "待补收货地址", receiver_address: null})], "confirm-receiver");
  api.bulkAction.mockResolvedValue({results: [result("a")]});
  fireEvent.click(screen.getByRole("button", {name: "执行本阶段未成功项"}));
  expect(api.bulkAction).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", {name: "确认并生成推广单"}));
  await waitFor(() => expect(api.bulkAction).toHaveBeenCalledWith("confirm-receiver", [{negotiation_id: "a", payload: {receiver_name: "甲", receiver_phone: "13800000000", receiver_address: "示例地址"}}]));
  expect(await screen.findByText("未执行：请填写：收件地址")).toBeInTheDocument();
});
it("support只读不能执行批量动作", async () => {
  access.readonly = true; await show([row("a")]);
  expect(screen.getByRole("button", {name: "执行本阶段未成功项"})).toBeDisabled();
});
