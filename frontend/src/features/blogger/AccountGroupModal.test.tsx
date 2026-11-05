// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {afterEach, beforeAll, beforeEach, expect, it, vi} from "vitest";
import {AccountGroupModal} from "./AccountGroupModal";

const api = vi.hoisted(() => ({getAccountGroup: vi.fn(), saveAccountGroup: vi.fn(), saveAccountQuote: vi.fn()}));
vi.mock("./accounts", async original => ({...await original<typeof import("./accounts")>(), ...api}));
vi.mock("@/components/RemoteSelect/BloggerSelect", () => ({BloggerSelect: () => <span>账号搜索</span>}));
const support = vi.hoisted(() => ({readonly: false}));
vi.mock("@/stores/supportStore", () => ({useReadOnlySupport: () => support.readonly}));
beforeAll(() => {const style = window.getComputedStyle.bind(window); window.getComputedStyle = el => style(el); Object.defineProperty(window, "matchMedia", {writable: true, value: () => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}})});});
beforeEach(() => {vi.clearAllMocks(); support.readonly = false;});
afterEach(cleanup);
const data = {group: {id: "group", name: "同人甲", account_ids: ["a", "b"], quotes: [{id: "quote", account_ids: ["a", "b"], amount: "0.00"}]}, accounts: [
  {id: "a", nickname: "甲A", platform: "抖音", account: "account-a", available_for_new_cooperation: true},
  {id: "b", nickname: "甲B", platform: "抖音", account: "account-b", available_for_new_cooperation: true},
], can_manage: true, can_read_quote: true, can_write_quote: true, can_write_amount: true};
function show() {render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}><AccountGroupModal target={{id: "a", nickname: "甲A"}} onClose={() => {}} /></QueryClientProvider>);}
it("同平台两个具体账号可维护，0组合价可见，保存关联不改旧报价", async () => {
  api.getAccountGroup.mockResolvedValue(data); api.saveAccountGroup.mockResolvedValue(data);
  show();
  expect(await screen.findByText("¥0.00")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("同人账号组名称"), {target: {value: "改名"}});
  fireEvent.click(screen.getByRole("button", {name: "保存账号关联"}));
  await waitFor(() => expect(api.saveAccountGroup).toHaveBeenCalledWith("group", {name: "改名", account_ids: ["a", "b"]}));
  expect(api.saveAccountQuote).not.toHaveBeenCalled();
});
it("金额deny隐藏组合金额和标价写入口", async () => {
  api.getAccountGroup.mockResolvedValue({...data, can_read_quote: false, can_write_quote: false}); show();
  expect(await screen.findByText("当前权限不可查看标价")).toBeInTheDocument();
  expect(screen.queryByText("¥0.00")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "保存组合标价"})).not.toBeInTheDocument();
});
it("保存具体A+B组合的0标价，不把账号平台当组合键", async () => {
  api.getAccountGroup.mockResolvedValue(data); api.saveAccountQuote.mockResolvedValue(data); show();
  await screen.findByText("¥0.00");
  fireEvent.mouseDown(screen.getByLabelText("本次标价适用账号"));
  fireEvent.click(screen.getAllByText("甲A · 抖音 · account-a").at(-1)!);
  fireEvent.click(screen.getAllByText("甲B · 抖音 · account-b").at(-1)!);
  fireEvent.change(screen.getByLabelText("组合标价"), {target: {value: "0"}});
  fireEvent.click(screen.getByRole("button", {name: "保存组合标价"}));
  await waitFor(() => expect(api.saveAccountQuote).toHaveBeenCalledWith("group", {account_ids: ["a", "b"], amount: "0"}));
});
it("support只读没有关联和报价写入口", async () => {
  support.readonly = true; api.getAccountGroup.mockResolvedValue(data); show();
  await screen.findByText("¥0.00");
  expect(screen.queryByRole("button", {name: "保存账号关联"})).not.toBeInTheDocument();
  expect(screen.queryByRole("button", {name: "保存组合标价"})).not.toBeInTheDocument();
});
