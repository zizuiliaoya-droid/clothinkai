import { apiClient } from "@/services/apiClient";

export interface RegisteredPermission {
  scope: string;
  name: string;
  category: string;
}

export interface UserPermissions {
  user_id: string;
  role_scopes: string[];
  grants: string[];
  revokes: string[];
  effective: string[];
}

export type PermissionAction = "grant" | "revoke" | "reset";

export async function listRegisteredPermissions(): Promise<RegisteredPermission[]> {
  const response = await apiClient.get<RegisteredPermission[]>("/api/permissions");
  return response.data;
}

export async function getUserPermissions(userId: string): Promise<UserPermissions> {
  const response = await apiClient.get<UserPermissions>(`/api/users/${userId}/effective-permissions`);
  return response.data;
}

export async function changeUserPermission(
  userId: string,
  action: PermissionAction,
  payload: { scope: string; reason?: string },
): Promise<void> {
  // revoke 是个人拒绝；reset 才是删除覆盖、恢复角色默认。
  await apiClient.post(`/api/users/${userId}/permissions/${action}`, payload);
}
