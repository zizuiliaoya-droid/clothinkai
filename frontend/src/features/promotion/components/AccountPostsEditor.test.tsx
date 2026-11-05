// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {afterEach, beforeAll, expect, it, vi} from "vitest";
import {PlatformPostsEditor} from "./PlatformPostsEditor";
import type {Promotion} from "../types";

const save = vi.hoisted(() => vi.fn());
vi.mock("../api", () => ({replacePlatformPosts: save}));
beforeAll(() => {const style = window.getComputedStyle.bind(window); window.getComputedStyle = el => style(el); Object.defineProperty(window, "matchMedia", {writable: true, value: () => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}})});});
afterEach(() => {cleanup(); save.mockReset();});
const accounts = [
  {account_id: "a", nickname: "甲A", account: "account-a", platform: "抖音"},
  {account_id: "b", nickname: "甲B", account: "account-b", platform: "抖音"},
];
function show(published = true, editable = true) {
  const promotion = {id: "p", platform: "抖音", publish_status: published ? "已发布" : "未发布", settlement_status: "待付款", account_snapshot: {version: 1, primary_account_id: "a", accounts},
    fulfillment: {total: 2, completed: 1, complete: false, missing_accounts: [accounts[1]]},
    platform_posts: accounts.map((a, i) => ({...a, id: a.account_id, is_primary: i === 0, publish_url: i ? null : "https://example.com/a", actual_publish_date: i ? null : "2026-10-20", like_count: 0})),
    ui: {fields: {platform_posts: {state: editable ? "edit" : "read"}}},
  } as unknown as Promotion;
  render(<QueryClientProvider client={new QueryClient({defaultOptions: {mutations: {retry: false}}})}><PlatformPostsEditor promotion={promotion} /></QueryClientProvider>);
}
it("同平台账号分别展示缺失情况，不能删除或增加约定义务", async () => {
  show();
  expect(screen.getByText("已完成 1 / 2 个约定账号发布")).toBeInTheDocument();
  expect(screen.getByText(/待补：甲B · 抖音 · account-b/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "编辑约定账号发布"}));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).queryByRole("button", {name: /删除|添加/})).not.toBeInTheDocument();
  expect(within(dialog).getAllByLabelText("发布链接")).toHaveLength(2);
});
it("进入结款后仍允许更正有效发布事实，保存完整账号集合和0点赞", async () => {
  save.mockResolvedValue({}); show();
  fireEvent.click(screen.getByRole("button", {name: "编辑约定账号发布"}));
  const dialog = await screen.findByRole("dialog");
  fireEvent.change(within(dialog).getAllByLabelText("发布链接")[0], {target: {value: "https://example.com/corrected"}});
  fireEvent.click(within(dialog).getByRole("button", {name: /保.*存/}));
  await waitFor(() => expect(save).toHaveBeenCalledWith("p", [
    {account_id: "a", platform: "抖音", publish_url: "https://example.com/corrected", actual_publish_date: "2026-10-20", like_count: 0},
    {account_id: "b", platform: "抖音", publish_url: null, actual_publish_date: null, like_count: 0},
  ]));
});
it("主账号未发布时不能绕过标记发布；其他账号可先录入", async () => {
  show(false);
  fireEvent.click(screen.getByRole("button", {name: "编辑约定账号发布"}));
  const dialog = await screen.findByRole("dialog");
  const urls = within(dialog).getAllByLabelText("发布链接");
  expect(urls[0]).toBeDisabled(); expect(urls[1]).not.toBeDisabled();
  expect(within(dialog).getByText(/主账号请先通过“标记发布”登记/)).toBeInTheDocument();
});
it("只读权限没有发布编辑入口", () => {
  show(true, false);
  expect(screen.queryByRole("button", {name: "编辑约定账号发布"})).not.toBeInTheDocument();
});
