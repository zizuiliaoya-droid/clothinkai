// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { UserListPage } from "@/pages/UserListPage";
import { useAuthStore } from "@/stores/authStore";
import { assignUserRoles, getUserManagementCapabilities, listUsers } from "./api";
import { listRegisteredPermissions } from "./permissionsApi";

vi.mock("./api", () => ({ listUsers: vi.fn(), getUserManagementCapabilities: vi.fn(), assignUserRoles: vi.fn(), createUser: vi.fn(), resetPassword: vi.fn(), toggleUser: vi.fn(), unlockUser: vi.fn() }));
vi.mock("./permissionsApi", () => ({ listRegisteredPermissions: vi.fn(), getUserPermissions: vi.fn(), changeUserPermission: vi.fn() }));

let targetId: string;
let roles: string[];
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (element: Element) => getStyle(element);
  Object.defineProperty(window, "matchMedia", { writable: true, value: (query: string) => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false }) });
});
beforeEach(() => {
  vi.clearAllMocks();
  targetId = "target";
  roles = ["pr_manager"];
  useAuthStore.setState({ user: { id: "manager", username: "管理员", display_name: null, email: null, status: "active", roles: ["designer"] }, isAuthenticated: true });
  vi.mocked(getUserManagementCapabilities).mockResolvedValue({ can_assign_roles: true, can_create_users: true });
  vi.mocked(listRegisteredPermissions).mockRejectedValue({ response: { status: 403 } });
  vi.mocked(listUsers).mockImplementation(async () => ({ items: [{ id: targetId, username: "测试用户", display_name: null, email: null, status: "active", locked_at: null, last_login_at: null, roles, created_at: "2026-10-31" }], meta: { page: 1, page_size: 20, total: 1 } }));
  vi.mocked(assignUserRoles).mockImplementation(async (_id, nextRoles) => { roles = nextRoles; });
});
afterEach(cleanup);
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><UserListPage /></QueryClientProvider>);
}
async function openAndAddFinance() {
  fireEvent.click(await screen.findByRole("button", { name: "修改角色" }));
  const dialog = within(await screen.findByRole("dialog"));
  fireEvent.mouseDown(dialog.getByRole("combobox"));
  fireEvent.click(await screen.findByText("财务", { selector: ".ant-select-item-option-content" }));
  return dialog;
}

describe("已有账号角色分配", () => {
  it("按实际权限显示入口，保存财务与PR主管并刷新中文角色", async () => {
    mount();
    const dialog = await openAndAddFinance();
    expect(dialog.getByText("PR 主管", { selector: ".ant-select-selection-item-content" })).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "保存角色" }));
    await waitFor(() => expect(assignUserRoles).toHaveBeenCalledWith("target", ["pr_manager", "finance"]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByText("财务", { selector: ".ant-tag" })).toBeTruthy();
    expect(listUsers).toHaveBeenCalledTimes(2);
  });
  it("管理员角色也不能越过后端拒绝的入口", async () => {
    useAuthStore.setState({ user: { ...useAuthStore.getState().user!, roles: ["admin"] } });
    vi.mocked(getUserManagementCapabilities).mockResolvedValue({ can_assign_roles: false, can_create_users: false });
    mount();
    await screen.findByText("测试用户");
    expect(screen.queryByRole("button", { name: "修改角色" })).toBeNull();
  });
  it("入口加载失败可重试，不先展示编辑按钮", async () => {
    vi.mocked(getUserManagementCapabilities).mockRejectedValueOnce(new Error("network"));
    mount();
    expect(await screen.findByText("角色管理入口加载失败")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "修改角色" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /重\s*试/ }));
    expect(await screen.findByRole("button", { name: "修改角色" })).toBeTruthy();
  });
  it("保存失败保留选择，允许重试", async () => {
    vi.mocked(assignUserRoles).mockRejectedValueOnce(new Error("save failed"));
    mount();
    const dialog = await openAndAddFinance();
    fireEvent.click(dialog.getByRole("button", { name: "保存角色" }));
    expect(await dialog.findByText("save failed")).toBeTruthy();
    expect(dialog.getByText("财务", { selector: ".ant-select-selection-item-content" })).toBeTruthy();
    fireEvent.click(dialog.getByRole("button", { name: "保存角色" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
  it("修改本人角色后清除登录状态", async () => {
    targetId = "manager";
    mount();
    const dialog = await openAndAddFinance();
    fireEvent.click(dialog.getByRole("button", { name: "保存角色" }));
    await waitFor(() => expect(useAuthStore.getState().isAuthenticated).toBe(false));
  });
  it("登录身份变化后旧用户编辑框关闭", async () => {
    mount();
    await openAndAddFinance();
    act(() => useAuthStore.setState({ user: { ...useAuthStore.getState().user!, id: "other" } }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(assignUserRoles).not.toHaveBeenCalled();
  });
});
