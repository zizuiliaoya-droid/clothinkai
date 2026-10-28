// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/services/apiClient";
import { changeUserPermission, getUserPermissions, listRegisteredPermissions } from "./permissionsApi";

vi.mock("@/services/apiClient", () => ({ apiClient: { get: vi.fn(), post: vi.fn() } }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(apiClient.post).mockResolvedValue({ data: { ok: true } });
});

describe("个人权限 API 合同", () => {
  it("读取真实注册目录与目标用户覆盖", async () => {
    const catalog = [{ scope: "negotiation_all:read", name: "查看全部谈款", category: "function" }];
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: catalog });
    expect(await listRegisteredPermissions()).toEqual(catalog);
    expect(apiClient.get).toHaveBeenLastCalledWith("/api/permissions");
    const permissions = { user_id: "u1", role_scopes: [], grants: [], revokes: [], effective: [] };
    vi.mocked(apiClient.get).mockResolvedValueOnce({ data: permissions });
    expect(await getUserPermissions("u1")).toEqual(permissions);
    expect(apiClient.get).toHaveBeenLastCalledWith("/api/users/u1/effective-permissions");
  });

  it.each(["grant", "revoke", "reset"] as const)("%s 保留独立语义、目标和原因", async (action) => {
    const payload = { scope: "negotiation.review:approve", reason: "主管请假，人工交接" };
    await changeUserPermission("u2", action, payload);
    expect(apiClient.post).toHaveBeenCalledWith(`/api/users/u2/permissions/${action}`, payload);
  });

  it("服务端失败保留原错误供界面显示", async () => {
    const error = { response: { status: 403, data: { code: "PERMISSION_DENIED", message: "没有授权权限" } } };
    vi.mocked(apiClient.post).mockRejectedValue(error);
    await expect(changeUserPermission("u1", "grant", { scope: "negotiation_all:read" })).rejects.toBe(error);
  });
});
