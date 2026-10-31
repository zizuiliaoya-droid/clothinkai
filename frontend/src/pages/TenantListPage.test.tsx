// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { TenantListPage } from "./TenantListPage";
import { createTenant, listTenants, setTenantStatus } from "@/features/tenant/api";
import { useAuthStore } from "@/stores/authStore";
import { createSupportSession } from "@/features/tenant/supportApi";
import { useSupportStore } from "@/stores/supportStore";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/features/tenant/supportApi", () => ({ createSupportSession: vi.fn() }));
vi.mock("@/features/tenant/api", () => ({ createTenant: vi.fn(), listTenants: vi.fn(), setTenantStatus: vi.fn() }));

const tenant = { id: "a", name: "客户 A", code: "client-a", status: "active", created_at: "2026-10-31T10:00:00Z" };
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (element: Element) => getStyle(element);
  Object.defineProperty(window, "matchMedia", { writable: true, value: (query: string) => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false }) });
});
beforeEach(() => {
  vi.clearAllMocks();
  useSupportStore.getState().end();
  useAuthStore.setState({ user: { id: "platform", username: "admin", display_name: null, email: null, status: "active", roles: ["admin", "platform_admin"] }, isAuthenticated: true });
  vi.mocked(listTenants).mockResolvedValue({ items: [{ ...tenant, id: "default", name: "平台", code: "default" }, tenant], meta: { page: 1, page_size: 20, total: 2 } });
  vi.mocked(createTenant).mockResolvedValue({ tenant, admin_username: "admin", initial_password: "test-one-time-password", must_change_password: true });
  vi.mocked(setTenantStatus).mockResolvedValue({ ...tenant, status: "disabled" });
});
afterEach(() => { cleanup(); useSupportStore.getState().end(); });

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><TenantListPage /></MemoryRouter></QueryClientProvider>);
  return client;
}
async function fillCreate() {
  fireEvent.click(await screen.findByRole("button", { name: /新增租户/ }));
  const dialog = within(await screen.findByRole("dialog"));
  fireEvent.change(dialog.getByLabelText("租户名称"), { target: { value: "客户 A" } });
  fireEvent.change(dialog.getByLabelText("租户代码"), { target: { value: "client-a" } });
  fireEvent.click(dialog.getByRole("button", { name: "创建租户" }));
}

describe("租户管理", () => {
  it("普通管理员不能看到入口或请求租户列表", () => {
    useAuthStore.setState({ user: { ...useAuthStore.getState().user!, roles: ["admin"] } });
    mount();
    expect(screen.getByText("仅平台管理员可管理租户")).toBeTruthy();
    expect(listTenants).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /新增租户/ })).toBeNull();
  });
  it("开户一次显示凭据与登录链接，关闭即清除", async () => {
    const client = mount();
    await fillCreate();
    expect(await screen.findByText("test-one-time-password")).toBeTruthy();
    expect(vi.mocked(createTenant).mock.calls[0][0]).toEqual({ code: "client-a", name: "客户 A", admin_username: "admin" });
    expect(screen.getByText(`${window.location.origin}/login?tenant=client-a`)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "已保存，关闭" }));
    await waitFor(() => expect(screen.queryByText("test-one-time-password")).toBeNull());
    await waitFor(() => expect(client.getMutationCache().getAll()).toHaveLength(0));
  });
  it("开户失败保留输入且不显示凭据", async () => {
    vi.mocked(createTenant).mockRejectedValue(new Error("租户代码已存在"));
    mount();
    await fillCreate();
    expect(await screen.findByText("租户代码已存在")).toBeTruthy();
    expect((screen.getByLabelText("租户代码") as HTMLInputElement).value).toBe("client-a");
    expect(screen.queryByText("test-one-time-password")).toBeNull();
  });
  it("只为客户租户提供停用，确认后发送明确状态", async () => {
    mount();
    const button = await screen.findByRole("button", { name: /停\s*用/ });
    expect(screen.getAllByRole("button", { name: /停\s*用/ })).toHaveLength(1);
    fireEvent.click(button);
    fireEvent.click(await screen.findByRole("button", { name: "OK" }));
    await waitFor(() => expect(setTenantStatus).toHaveBeenCalledWith("a", "disabled"));
  });
  it("账号变化时立即销毁已展示的初始密码", async () => {
    mount();
    await fillCreate();
    await screen.findByText("test-one-time-password");
    act(() => useAuthStore.setState({ user: { ...useAuthStore.getState().user!, id: "customer", roles: ["admin"] } }));
    await waitFor(() => expect(screen.queryByText("test-one-time-password")).toBeNull());
    expect(screen.getByText("仅平台管理员可管理租户")).toBeTruthy();
  });
});

it("选择客户签发短时只读 token，不替换平台登录，停用客户不能进入", async () => {
  vi.mocked(listTenants).mockResolvedValue({ items: [tenant, { ...tenant, id: "b", name: "客户 B", status: "disabled" }], meta: { page: 1, page_size: 20, total: 2 } });
  vi.mocked(createSupportSession).mockResolvedValue({ access_token: "support-test", expires_in: 900, tenant });
  mount();
  const buttons = await screen.findAllByRole("button", { name: "只读排查" });
  expect((buttons[1] as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(buttons[0]);
  await waitFor(() => expect(createSupportSession).toHaveBeenCalledWith("a"));
  await waitFor(() => expect(useSupportStore.getState().session?.tenant.id).toBe("a"));
  expect(useAuthStore.getState().user?.id).toBe("platform");
});
it("签发失败停留平台并展示错误", async () => {
  vi.mocked(createSupportSession).mockRejectedValue(new Error("客户已停用"));
  mount();
  fireEvent.click((await screen.findAllByRole("button", { name: "只读排查" }))[1]);
  expect(await screen.findByText("客户已停用")).toBeTruthy();
  expect(useSupportStore.getState().session).toBeNull();
});
