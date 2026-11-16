// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { message } from "antd";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, expect, it, vi } from "vitest";
import { BloggerListPage } from "./BloggerListPage";

const api = vi.hoisted(() => ({createBlogger: vi.fn(), listBloggers: vi.fn(), listBloggerTags: vi.fn(), updateBlogger: vi.fn(), disableBlogger: vi.fn(), restoreBlogger: vi.fn()}));
const claims = vi.hoisted(() => ({getClaimSettings: vi.fn(), listWorkWechats: vi.fn(), enableClaims: vi.fn()}));
const importer = vi.hoisted(() => ({uploadImportFile: vi.fn(), downloadImportTemplate: vi.fn()}));
vi.mock("@/features/blogger/api", () => api);
vi.mock("@/features/blogger/claims", () => claims);
vi.mock("@/features/import/api", () => importer);
vi.mock("@/stores/supportStore", () => ({useReadOnlySupport: () => false}));
vi.mock("@/stores/authStore", () => ({useAuthStore: (select: (state: {user: {id: string; roles: string[]}}) => unknown) => select({user: {id: "pr1", roles: ["pr"]}})}));
vi.mock("@/components/BloggerTagDict/BloggerTagDictModal", () => ({BloggerTagDictModal: () => null}));
vi.mock("@/features/blogger/WorkWechatModal", () => ({WorkWechatModal: () => null}));
vi.mock("@/features/blogger/BloggerClaimsModal", () => ({BloggerClaimsModal: () => null}));

const clients: QueryClient[] = [];
beforeAll(() => {
  const style = window.getComputedStyle.bind(window);
  window.getComputedStyle = element => style(element);
  Object.defineProperty(window, "matchMedia", {writable: true, value: () => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}})});
});
beforeEach(() => {
  vi.clearAllMocks();
  api.listBloggers.mockResolvedValue({items: [], total: 0, page: 1, page_size: 10});
  api.listBloggerTags.mockResolvedValue({items: [], system_tags: []});
  api.createBlogger.mockResolvedValue({id: "new"});
  claims.listWorkWechats.mockResolvedValue({items: [], can_manage: false});
  importer.uploadImportFile.mockResolvedValue({batch_id: "batch1"});
});
afterEach(() => {cleanup(); message.destroy(); clients.splice(0).forEach(c => c.clear());});

async function show(enabled: boolean) {
  claims.getClaimSettings.mockResolvedValue({enabled_at: enabled ? "2026-11-05T12:00:00Z" : null, unclaimed_count: 0, pending_work_wechat_count: 0, can_enable: false});
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  clients.push(client);
  render(<QueryClientProvider client={client}><BloggerListPage /></QueryClientProvider>);
  await screen.findByText(enabled ? /认领规则已启用：/ : /准备分配中：/);
}

it.each([false, true])("空工作微信字典，新建在enabled=%s时遵守准备期兼容", async enabled => {
  await show(enabled);
  fireEvent.click(screen.getByRole("button", {name: /新建博主/}));
  fireEvent.change(screen.getByLabelText("账号"), {target: {value: "xhs-new"}});
  fireEvent.change(screen.getByLabelText("昵称"), {target: {value: "新博主"}});
  fireEvent.submit(document.querySelector(".ant-modal-body form")!);
  if (enabled) {
    expect(await screen.findByText("请选择认领使用的工作微信")).toBeTruthy();
    expect(api.createBlogger).not.toHaveBeenCalled();
  } else {
    await waitFor(() => expect(api.createBlogger).toHaveBeenCalledWith(expect.objectContaining({xiaohongshu_id: "xhs-new", nickname: "新博主", platform: "抖音"})));
    expect(api.createBlogger.mock.calls[0][0].claim_work_wechat_id).toBeUndefined();
  }
});

it.each([
  [false, "manual_blogger", "导入博主库"], [true, "manual_blogger", "导入博主库"],
] as const)("空工作微信字典，enabled=%s的%s上传行为", async (enabled, source, label) => {
  await show(enabled);
  const file = new File(["账号,昵称\na,b"], "bloggers.csv", {type: "text/csv"});
  const input = screen.getByRole("button", {name: new RegExp(label)}).closest(".ant-upload")!.querySelector("input[type=file]")!;
  fireEvent.change(input, {target: {files: [file]}});
  if (enabled) {
    expect(await screen.findByText("请先选择本批认领使用的工作微信")).toBeTruthy();
    expect(importer.uploadImportFile).not.toHaveBeenCalled();
  } else {
    await waitFor(() => expect(importer.uploadImportFile).toHaveBeenCalledWith(source, expect.any(File)));
  }
});


it("旧来源停止新上传，人工模板继续可用", async () => {
  await show(false);
  expect(screen.queryByRole("button", {name: /导入灰豚/})).not.toBeInTheDocument();
  expect(screen.getByRole("button", {name: /导入博主库/})).toBeInTheDocument();
});

it.each([
  ["账号", "中文账号", "账号只能包含字母、数字、_ . -"],
  ["昵称", "   ", "昵称需填写 1–128 个字符"],
  ["昵称", "长".repeat(129), "昵称需填写 1–128 个字符"],
  ["粉丝数", "1.5", "粉丝数必须为非负整数"],
])("保存前提示具体无效字段：%s", async (label, value, error) => {
  await show(false);
  fireEvent.click(screen.getByRole("button", {name: /新建博主/}));
  fireEvent.change(screen.getByLabelText("账号"), {target: {value: "normal_01"}});
  fireEvent.change(screen.getByLabelText("昵称"), {target: {value: "正常昵称"}});
  fireEvent.change(screen.getByLabelText(label), {target: {value}});
  fireEvent.submit(document.querySelector(".ant-modal-body form")!);
  expect(await screen.findByText(error)).toBeInTheDocument();
  expect(api.createBlogger).not.toHaveBeenCalled();
});

it("后端422详情显示字段原因并保留表单供修改", async () => {
  api.createBlogger.mockRejectedValueOnce({response: {data: {
    code: "VALIDATION_ERROR", message: "请求数据校验失败",
    details: {errors: [{loc: ["body", "wechat"], type: "string_too_long", msg: "String should have at most 64 characters", ctx: {max_length: 64}}]},
  }}});
  await show(false);
  fireEvent.click(screen.getByRole("button", {name: /新建博主/}));
  fireEvent.change(screen.getByLabelText("账号"), {target: {value: "normal_01"}});
  fireEvent.change(screen.getByLabelText("昵称"), {target: {value: "正常昵称"}});
  fireEvent.submit(document.querySelector(".ant-modal-body form")!);
  expect((await screen.findAllByText("微信最多 64 个字符")).length).toBeGreaterThan(0);
  expect(screen.getByLabelText("账号")).toHaveValue("normal_01");
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("编辑旧小红书账号保留平台并允许历史中文账号原样保存", async () => {
  api.listBloggers.mockResolvedValue({items: [{
    id: "existing", xiaohongshu_id: "历史中文账号", nickname: "原昵称", platform: "小红书",
    is_active: true, category_tags: [], quality_tags: [], quote_note: null,
  }], total: 1, page: 1, page_size: 10});
  api.updateBlogger.mockResolvedValue({id: "existing"});
  await show(false);
  fireEvent.click(await screen.findByRole("button", {name: "编辑"}));
  expect(screen.getByLabelText("账号")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("昵称"), {target: {value: "修改昵称"}});
  fireEvent.submit(document.querySelector(".ant-modal-body form")!);
  await waitFor(() => expect(api.updateBlogger).toHaveBeenCalledWith("existing", expect.objectContaining({
    xiaohongshu_id: "历史中文账号", nickname: "修改昵称", platform: "小红书",
  })));
  expect(api.createBlogger).not.toHaveBeenCalled();
});
