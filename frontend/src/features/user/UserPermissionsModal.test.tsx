// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { UserListPage } from "@/pages/UserListPage";
import { useAuthStore } from "@/stores/authStore";
import { changeUserPermission, getUserPermissions, listRegisteredPermissions } from "./permissionsApi";
import type { UserPermissions } from "./permissionsApi";
import { listUsers } from "./api";

vi.mock("./api", () => ({ listUsers: vi.fn(), createUser: vi.fn(), resetPassword: vi.fn(), toggleUser: vi.fn(), unlockUser: vi.fn() }));
vi.mock("./permissionsApi", () => ({ listRegisteredPermissions: vi.fn(), getUserPermissions: vi.fn(), changeUserPermission: vi.fn() }));

const scopes = ["negotiation.review:approve", "negotiation_all:read"];
const catalog = [
  { scope: scopes[0], name: "谈款主管审核", category: "function" },
  { scope: scopes[1], name: "查看全部谈款", category: "function" },
];
let snapshots: Record<string, UserPermissions>;

beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (element: Element) => getStyle(element);
  Object.defineProperty(window, "matchMedia", { writable: true, value: (query: string) => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false }) });
});

beforeEach(() => {
  vi.clearAllMocks();
  useAuthStore.setState({ user: { id: "manager", username: "管理员", display_name: null, email: null, status: "active", roles: ["designer"] }, isAuthenticated: true });
  vi.mocked(listUsers).mockResolvedValue({
    items: ["a", "b"].map((id) => ({ id, username: `用户${id}`, display_name: null, email: null, status: "active", locked_at: null, last_login_at: null, roles: ["pr"], created_at: "2026-10-27" })),
    meta: { page: 1, page_size: 20, total: 2 },
  });
  snapshots = Object.fromEntries(["a", "b"].map((id) => [id, { user_id: id, role_scopes: ["negotiation:read"], grants: [], revokes: [], effective: ["negotiation:read"] }]));
  vi.mocked(listRegisteredPermissions).mockResolvedValue(catalog);
  vi.mocked(getUserPermissions).mockImplementation(async (id) => ({ ...snapshots[id] }));
  vi.mocked(changeUserPermission).mockImplementation(async (id, action, { scope }) => {
    const snapshot = snapshots[id];
    snapshot.grants = snapshot.grants.filter((item) => item !== scope);
    snapshot.revokes = snapshot.revokes.filter((item) => item !== scope);
    if (action === "grant") snapshot.grants.push(scope);
    if (action === "revoke") snapshot.revokes.push(scope);
  });
});

afterEach(cleanup);

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><UserListPage /></QueryClientProvider>);
  return client;
}

async function openUser(index = 0) {
  fireEvent.click((await screen.findAllByRole("button", { name: "个人权限" }))[index]);
  return screen.findByRole("dialog");
}

async function selectPermission(index = 0) {
  const select = await within(screen.getByRole("dialog")).findByRole("combobox");
  await waitFor(() => expect(select.hasAttribute("disabled")).toBe(false));
  fireEvent.mouseDown(select);
  fireEvent.click(await screen.findByText(`${catalog[index].name}（${catalog[index].scope}）`, { selector: ".ant-select-item-option-content" }));
}

async function savePermission(index = 0, deny = false) {
  await selectPermission(index);
  const dialog = within(screen.getByRole("dialog"));
  fireEvent.click(dialog.getByRole("radio", { name: deny ? "拒绝" : "授予" }));
  fireEvent.change(dialog.getByLabelText("原因"), { target: { value: "  主管请假，手工交接  " } });
  fireEvent.click(dialog.getByRole("button", { name: "保存个人设置" }));
}

describe("用户管理个人权限闭环", () => {
  it.each(scopes.map((scope, index) => ({ scope, index })))("按真实目录授予并恢复临时代理权限 $scope", async ({ scope, index }) => {
    mount();
    const dialog = within(await openUser());
    expect(await dialog.findByText("暂无个人覆盖，使用角色默认权限")).toBeTruthy();
    expect(dialog.getByText(/不会自动到期/)).toBeTruthy();
    await savePermission(index);
    await waitFor(() => expect(changeUserPermission).toHaveBeenCalledWith("a", "grant", { scope, reason: "主管请假，手工交接" }));
    const reset = await dialog.findByRole("button", { name: `恢复角色默认 ${scope}` });
    expect(dialog.getAllByText("个人授予")).toHaveLength(1);
    await waitFor(() => expect(reset.hasAttribute("disabled")).toBe(false));
    expect(dialog.getByRole("button", { name: "保存个人设置" }).hasAttribute("disabled")).toBe(false);
    fireEvent.click(reset);
    fireEvent.click(await screen.findByRole("button", { name: "确认恢复" }));
    await waitFor(() => expect(changeUserPermission).toHaveBeenCalledWith("a", "reset", { scope, reason: "撤销个人覆盖，恢复角色默认" }));
    await waitFor(() => expect(dialog.queryByRole("button", { name: `恢复角色默认 ${scope}` })).toBeNull());
    expect(await dialog.findByText("暂无个人覆盖，使用角色默认权限")).toBeTruthy();
    expect(getUserPermissions).toHaveBeenCalledTimes(3);
  });

  it("已有另一项覆盖时，新增及恢复当前权限不改动原覆盖", async () => {
    snapshots.a.grants = [scopes[0]];
    mount();
    const dialog = within(await openUser());
    await savePermission(1);
    await waitFor(() => expect(changeUserPermission).toHaveBeenCalledWith("a", "grant", { scope: scopes[1], reason: "主管请假，手工交接" }));
    const reset = await dialog.findByRole("button", { name: `恢复角色默认 ${scopes[1]}` });
    expect(dialog.getAllByText("个人授予")).toHaveLength(2);
    expect(dialog.getByRole("button", { name: `恢复角色默认 ${scopes[0]}` })).toBeTruthy();
    await waitFor(() => expect(reset.hasAttribute("disabled")).toBe(false));
    fireEvent.click(reset);
    fireEvent.click(await screen.findByRole("button", { name: "确认恢复" }));
    await waitFor(() => expect(changeUserPermission).toHaveBeenCalledWith("a", "reset", { scope: scopes[1], reason: "撤销个人覆盖，恢复角色默认" }));
    await waitFor(() => expect(dialog.queryByRole("button", { name: `恢复角色默认 ${scopes[1]}` })).toBeNull());
    expect(dialog.getByRole("button", { name: `恢复角色默认 ${scopes[0]}` })).toBeTruthy();
    expect(dialog.getAllByText("个人授予")).toHaveLength(1);
    expect(dialog.queryByText("暂无个人覆盖，使用角色默认权限")).toBeNull();
    expect(getUserPermissions).toHaveBeenCalledTimes(3);
  });

  it("拒绝走 revoke，已有 grant 更新为明确的个人拒绝状态", async () => {
    snapshots.a.grants = [scopes[0]];
    mount();
    await openUser();
    expect(await screen.findByText("个人授予")).toBeTruthy();
    await savePermission(0, true);
    await waitFor(() => expect(changeUserPermission).toHaveBeenCalledWith("a", "revoke", { scope: scopes[0], reason: "主管请假，手工交接" }));
    expect(await screen.findByText("个人拒绝")).toBeTruthy();
    expect(screen.queryByText("个人授予")).toBeNull();
  });

  it("缺少管理权限时隐藏入口，也不读取目标用户的权限", async () => {
    vi.mocked(listRegisteredPermissions).mockRejectedValue({ response: { status: 403, data: { code: "PERMISSION_DENIED", message: "缺少个人权限管理权限" } } });
    const client = mount();
    await waitFor(() => expect(client.getQueryState(["permission-catalog", "manager"])?.status).toBe("error"));
    expect(screen.queryByRole("button", { name: "个人权限" })).toBeNull();
    expect(getUserPermissions).not.toHaveBeenCalled();
    expect(changeUserPermission).not.toHaveBeenCalled();
  });

  it("目录读取失败原样提示，重试成功后恢复入口", async () => {
    vi.mocked(listRegisteredPermissions).mockRejectedValueOnce(new Error("权限目录暂时不可用"));
    mount();
    expect(await screen.findByText("权限目录暂时不可用")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "个人权限" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findAllByRole("button", { name: "个人权限" })).toHaveLength(2);
  });

  it("目标读取 403 时显示服务端原因且不能提交", async () => {
    vi.mocked(getUserPermissions).mockRejectedValue({ response: { status: 403, data: { code: "PERMISSION_DENIED", message: "管理授权已撤销" } } });
    mount();
    await openUser();
    expect(await screen.findByText("管理授权已撤销")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "保存个人设置" })).toBeNull();
    expect(changeUserPermission).not.toHaveBeenCalled();
  });

  it("写入失败保留覆盖与表单，原样显示服务端错误", async () => {
    snapshots.a.grants = [scopes[1]];
    vi.mocked(changeUserPermission).mockRejectedValue({ response: { status: 422, data: { code: "VALIDATION_ERROR", message: "此权限当前不可授予" } } });
    mount();
    await openUser();
    await savePermission();
    expect(await screen.findByText("此权限当前不可授予")).toBeTruthy();
    expect(screen.getByRole("button", { name: `恢复角色默认 ${scopes[1]}` })).toBeTruthy();
    expect((screen.getByLabelText("原因") as HTMLTextAreaElement).value).toContain("主管请假");
    expect(getUserPermissions).toHaveBeenCalledTimes(1);
  });

  it("原因必填，空白内容不会发起写入", async () => {
    mount();
    await openUser();
    await selectPermission();
    fireEvent.change(screen.getByLabelText("原因"), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "保存个人设置" }));
    expect(await screen.findByText("请填写本次设置的原因")).toBeTruthy();
    expect(changeUserPermission).not.toHaveBeenCalled();
  });

  it("登录账号切换后关闭旧目标，不能沿用旧管理入口与缓存", async () => {
    mount();
    await openUser();
    expect(await screen.findByText("暂无个人覆盖，使用角色默认权限")).toBeTruthy();
    vi.mocked(listRegisteredPermissions).mockRejectedValue({ response: { status: 403 } });
    act(() => useAuthStore.setState({ user: { id: "new-viewer", username: "新查看者", display_name: null, email: null, status: "active", roles: ["pr"] } }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByRole("button", { name: "个人权限" })).toBeNull();
    expect(getUserPermissions).toHaveBeenCalledTimes(1);
  });

  it("关闭后切到另一用户，旧读取迟到也不串数据或提交目标", async () => {
    let resolveOld!: (value: UserPermissions) => void;
    vi.mocked(getUserPermissions).mockImplementation((id) => id === "a" ? new Promise((resolve) => { resolveOld = resolve; }) : Promise.resolve(snapshots.b));
    mount();
    const first = await openUser();
    await waitFor(() => expect(getUserPermissions).toHaveBeenCalledWith("a"));
    fireEvent.click(within(first).getByRole("button", { name: /关\s*闭/ }));
    await openUser(1);
    expect(await screen.findByText("暂无个人覆盖，使用角色默认权限")).toBeTruthy();
    await act(async () => resolveOld({ ...snapshots.a, grants: ["old.private:read"] }));
    expect(screen.queryByText("old.private:read")).toBeNull();
    await savePermission();
    await waitFor(() => expect(changeUserPermission).toHaveBeenCalledWith("b", "grant", expect.objectContaining({ scope: scopes[0] })));
  });
});
