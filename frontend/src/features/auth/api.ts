// auth feature 的 API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  ChangePasswordRequest,
  LoginRequest,
  TokenPair,
  UserSummary,
} from "@/types";

export async function login(payload: LoginRequest): Promise<TokenPair> {
  const resp = await apiClient.post<TokenPair>("/api/auth/login", payload);
  return resp.data;
}

export async function logout(): Promise<void> {
  await apiClient.post("/api/auth/logout");
}

export async function getMe(): Promise<UserSummary> {
  const resp = await apiClient.get<UserSummary>("/api/auth/me");
  return resp.data;
}

export async function changePassword(payload: ChangePasswordRequest): Promise<void> {
  await apiClient.put("/api/auth/password", payload);
}
