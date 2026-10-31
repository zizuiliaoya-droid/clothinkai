// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { LoginForm } from "./LoginForm";

const mocks = vi.hoisted(() => ({ login: vi.fn(), getMe: vi.fn(), setSession: vi.fn(), setTokens: vi.fn() }));
vi.mock("../api", () => ({ login: mocks.login, getMe: mocks.getMe }));
vi.mock("@/stores/authStore", () => ({ useAuthStore: (selector: (s: {setSession: typeof mocks.setSession}) => unknown) => selector({ setSession: mocks.setSession }) }));
vi.mock("@/services/apiClient", () => ({ setTokens: mocks.setTokens, extractErrorMessage: () => "登录失败" }));

beforeAll(() => {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn() }));
});
beforeEach(() => {
  vi.clearAllMocks();
  mocks.login.mockResolvedValue({ access_token: "fake-access", refresh_token: "fake-refresh", must_change_password: true });
  mocks.getMe.mockResolvedValue({ id: "test-user", username: "admin", roles: ["admin"] });
});
afterEach(cleanup);

async function submit() {
  fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "SyntheticPwd123" } });
  fireEvent.click(screen.getByRole("button", { name: "登 录" }));
  await waitFor(() => expect(mocks.login).toHaveBeenCalledTimes(1));
}

describe("租户登录", () => {
  it("原客户不填租户代码仍以default登录", async () => {
    render(<MemoryRouter initialEntries={["/login"]}><LoginForm /></MemoryRouter>);
    await submit();
    expect(mocks.login).toHaveBeenCalledWith({ tenant_code: "default", username: "admin", password: "SyntheticPwd123" });
    await waitFor(() => expect(mocks.setSession).toHaveBeenCalledTimes(1));
  });
  it("开户链接预填租户代码，并允许改为另一租户", async () => {
    render(<MemoryRouter initialEntries={["/login?tenant=customer-a"]}><LoginForm /></MemoryRouter>);
    const tenantInput = screen.getByLabelText("租户代码") as HTMLInputElement;
    expect(tenantInput.value).toBe("customer-a");
    fireEvent.change(tenantInput, { target: { value: "customer-b" } });
    await submit();
    expect(mocks.login).toHaveBeenCalledWith({ tenant_code: "customer-b", username: "admin", password: "SyntheticPwd123" });
  });
  it("非法租户代码不提交", async () => {
    render(<MemoryRouter initialEntries={["/login?tenant=bad%2Fcode"]}><LoginForm /></MemoryRouter>);
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "SyntheticPwd123" } });
    fireEvent.click(screen.getByRole("button", { name: "登 录" }));
    await screen.findByText("仅支持字母、数字、下划线和短横线");
    expect(mocks.login).not.toHaveBeenCalled();
  });
});
