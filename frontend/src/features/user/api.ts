// user 管理 API（/api/users）。

import { apiClient } from "@/services/apiClient";

export interface UserListItem {
  id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  status: string;
  locked_at: string | null;
  last_login_at: string | null;
  roles: string[];
  created_at: string;
}

export interface UserListResponse {
  items: UserListItem[];
  meta: { page: number; page_size: number; total: number };
}

export interface UserCreatePayload {
  username: string;
  display_name?: string | null;
  email?: string | null;
  role_codes: string[];
}

export interface UserCreateResult {
  user: { id: string; username: string };
  initial_password: string;
}

export async function listUsers(params: {
  page?: number;
  page_size?: number;
  status?: string;
  search?: string;
} = {}): Promise<UserListResponse> {
  const resp = await apiClient.get<UserListResponse>("/api/users/", { params });
  return resp.data;
}

export async function createUser(
  payload: UserCreatePayload
): Promise<UserCreateResult> {
  const resp = await apiClient.post<UserCreateResult>("/api/users/", payload);
  return resp.data;
}

export async function toggleUser(userId: string): Promise<unknown> {
  const resp = await apiClient.put(`/api/users/${userId}/toggle`);
  return resp.data;
}

export async function unlockUser(userId: string): Promise<unknown> {
  const resp = await apiClient.put(`/api/users/${userId}/unlock`);
  return resp.data;
}

export async function resetPassword(
  userId: string
): Promise<{ initial_password: string }> {
  const resp = await apiClient.put<{ initial_password: string }>(
    `/api/users/${userId}/reset-password`
  );
  return resp.data;
}
