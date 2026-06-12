// 系统设置 API（企微配置）。

import { apiClient } from "@/services/apiClient";

export interface WecomConfig {
  corp_id: string;
  agent_id: string;
  secret_configured: boolean;
  callback_token: string | null;
  default_sender_userid: string | null;
  is_active: boolean;
}

export interface WecomConfigUpdate {
  corp_id: string;
  agent_id: string;
  secret: string;
  callback_token?: string | null;
  default_sender_userid?: string | null;
  is_active?: boolean;
}

export async function getWecomConfig(): Promise<WecomConfig | null> {
  try {
    const resp = await apiClient.get<WecomConfig>("/api/settings/wecom");
    return resp.data;
  } catch (e: unknown) {
    // 未配置时后端可能 404
    if (
      e &&
      typeof e === "object" &&
      "response" in e &&
      (e as { response?: { status?: number } }).response?.status === 404
    ) {
      return null;
    }
    throw e;
  }
}

export async function updateWecomConfig(
  payload: WecomConfigUpdate
): Promise<WecomConfig> {
  const resp = await apiClient.put<WecomConfig>("/api/settings/wecom", payload);
  return resp.data;
}

export async function testWecom(): Promise<{ ok: boolean; reason: string | null }> {
  const resp = await apiClient.post<{ ok: boolean; reason: string | null }>(
    "/api/settings/wecom/test"
  );
  return resp.data;
}
