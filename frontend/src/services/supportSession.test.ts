// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axios, { AxiosError, type InternalAxiosRequestConfig } from "axios";
import { apiClient, getAccessToken, getRefreshToken, setTokens } from "./apiClient";
import { queryClient } from "./queryClient";
import { useSupportStore } from "@/stores/supportStore";

const grant = { access_token: "support-only-token", expires_in: 900, tenant: { id: "client-a", code: "a", name: "客户 A" } };
function fail(config: InternalAxiosRequestConfig, status: number, code = "FORBIDDEN") {
  return new AxiosError("denied", undefined, config, undefined, { status, data: { code }, headers: {}, statusText: "denied", config });
}
beforeEach(() => { useSupportStore.getState().end(); localStorage.clear(); setTokens("platform-access", "platform-refresh"); });
afterEach(() => { useSupportStore.getState().end(); vi.restoreAllMocks(); queryClient.clear(); });

describe("限时客户只读会话", () => {
  it("切入和退出清理查询缓存，令牌只保存在当前内存，平台凭据始终保留", async () => {
    queryClient.setQueryData(["old-customer"], { private: true });
    useSupportStore.getState().start(grant);
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(getAccessToken()).toBe(grant.access_token);
    expect(getRefreshToken()).toBeNull();
    const adapter = vi.fn(async (config: InternalAxiosRequestConfig) => ({ config, data: {}, status: 200, statusText: "OK", headers: {} }));
    await apiClient.get("/api/goods/", { adapter });
    expect(adapter.mock.calls[0][0].headers.Authorization).toBe("Bearer support-only-token");
    expect(Object.values(localStorage)).not.toContain(grant.access_token);
    queryClient.setQueryData(["client-a"], { private: true });
    useSupportStore.getState().end();
    expect(queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(getAccessToken()).toBe("platform-access");
    expect(getRefreshToken()).toBe("platform-refresh");
  });

  it.each(["post", "put", "patch", "delete"])("只读模式在发送 %s 前阻止写请求", async (method) => {
    useSupportStore.getState().start(grant);
    const adapter = vi.fn();
    await expect(apiClient.request({ url: "/api/goods/", method, adapter })).rejects.toThrow("只读排查");
    expect(adapter).not.toHaveBeenCalled();
  });

  it("到期请求终止而不是自动改用平台 token", async () => {
    useSupportStore.getState().start({ ...grant, expires_in: -1 });
    const adapter = vi.fn();
    await expect(apiClient.get("/api/goods/", { adapter })).rejects.toThrow("到期");
    expect(adapter).not.toHaveBeenCalled();
    expect(useSupportStore.getState().session).toBeNull();
    expect(getRefreshToken()).toBe("platform-refresh");
  });

  it.each([[401, "UNAUTHORIZED"], [403, "SUPPORT_SESSION_INVALID"]])("%s %s 结束支持会话，无刷新/退出客户调用", async (status, code) => {
    const refresh = vi.spyOn(axios, "post");
    useSupportStore.getState().start(grant);
    await expect(apiClient.get("/api/goods/", { adapter: async (config) => { throw fail(config, status as number, code as string); } })).rejects.toThrow();
    expect(useSupportStore.getState().session).toBeNull();
    expect(refresh).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe("platform-access");
    expect(getRefreshToken()).toBe("platform-refresh");
  });

  it("普通路由 403 保持当前只读会话", async () => {
    useSupportStore.getState().start(grant);
    await expect(apiClient.get("/api/forbidden", { adapter: async (config) => { throw fail(config, 403); } })).rejects.toThrow();
    expect(useSupportStore.getState().session?.tenant.id).toBe("client-a");
  });

  it.each([false, true])("进入支持后丢弃旧平台请求的迟到响应(error=%s)，不清平台登录", async (error) => {
    let finish!: () => void;
    let request!: Promise<unknown>;
    const started = new Promise<void>((resolve) => {
      request = apiClient.get("/api/tenants", { adapter: (config) => new Promise((res, rej) => {
        finish = () => error ? rej(fail(config, 401)) : res({ config, status: 200, statusText: "OK", data: {}, headers: {} });
        resolve();
      }) });
    });
    await started;
    useSupportStore.getState().start(grant);
    finish();
    await expect(request).rejects.toThrow("会话已切换");
    expect(localStorage.getItem("clothing_erp_refresh_token")).toBe("platform-refresh");
    expect(useSupportStore.getState().session?.tenant.id).toBe("client-a");
  });
});
