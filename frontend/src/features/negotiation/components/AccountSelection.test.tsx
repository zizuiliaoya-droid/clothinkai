// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {cleanup, render, screen, waitFor} from "@testing-library/react";
import {Form, Input} from "antd";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {afterEach, beforeAll, beforeEach, expect, it, vi} from "vitest";
import {AccountSelection} from "./AccountSelection";
import type {NegotiationFormValues} from "../negotiationForm";

const api = vi.hoisted(() => ({getAccountGroup: vi.fn(), resolveAccountQuote: vi.fn()}));
vi.mock("@/features/blogger/accounts", async original => ({...await original<typeof import("@/features/blogger/accounts")>(), ...api}));
beforeAll(() => {Object.defineProperty(window, "matchMedia", {writable: true, value: () => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}})});});
afterEach(cleanup);
const reference = vi.fn(); const permissions = vi.fn();
const data = {group: {id: "g", name: "同人", account_ids: ["a", "b"], quotes: []}, accounts: [
  {id: "a", nickname: "甲A", account: "account-a", platform: "抖音", available_for_new_cooperation: true},
  {id: "b", nickname: "甲B", account: "account-b", platform: "抖音", available_for_new_cooperation: false},
], can_write_amount: true};
beforeEach(() => {vi.clearAllMocks(); api.getAccountGroup.mockResolvedValue(data);});
function Fields() {const [form] = Form.useForm<NegotiationFormValues>(); return <Form form={form} initialValues={{blogger_id: "a", account_ids: ["a", "b"]}}><Form.Item name="blogger_id" hidden><Input /></Form.Item><AccountSelection form={form} enabled legacy={false} onSelectionChange={() => {}} onReference={reference} onPermissions={permissions} /></Form>;}
function show() {render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}><Fields /></QueryClientProvider>);}
it("组合未命中保持未知，不把各账号报价相加", async () => {
  api.resolveAccountQuote.mockResolvedValue({matched: false, reference_amount: null, can_read_quote: true}); show();
  expect(await screen.findByText("尚未设置此组合报价，请填写本次成交金额")).toBeInTheDocument();
  await waitFor(() => expect(api.resolveAccountQuote).toHaveBeenCalledWith("a", ["a", "b"]));
  expect(reference).toHaveBeenCalledWith(null);
});
it("0参考价有效，同平台具体账号同时显示", async () => {
  api.resolveAccountQuote.mockResolvedValue({matched: true, reference_amount: "0.00", can_read_quote: true}); show();
  expect(await screen.findByText("本次参考标价 ¥0.00")).toBeInTheDocument();
  expect(screen.getAllByText(/甲A · 抖音 · account-a/).length).toBeGreaterThan(0);
  expect(screen.getByText(/甲B · 抖音 · account-b/)).toBeInTheDocument();
  expect(reference).toHaveBeenCalledWith("0.00");
});
it("有效权限禁止写金额时同步只读，不显示被隐藏的参考价", async () => {
  api.getAccountGroup.mockResolvedValue({...data, can_write_amount: false});
  api.resolveAccountQuote.mockResolvedValue({matched: true, reference_amount: null, can_read_quote: false}); show();
  expect(await screen.findByText("当前权限不可查看参考标价")).toBeInTheDocument();
  expect(permissions).toHaveBeenLastCalledWith(false, true);
  expect(screen.queryByText(/本次参考标价 ¥/)).not.toBeInTheDocument();
});
