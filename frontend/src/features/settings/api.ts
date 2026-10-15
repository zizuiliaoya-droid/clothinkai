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

// ---------- 平台指标（7e R7）：每个平台的爆文阈值与点赞折算系数 ----------

/** 一个平台的配置。系数是 3 位小数的十进制字符串（后端 Decimal，如 "0.100"），不经 float。 */
export interface PromotionPlatformSetting {
  platform: string;
  like_coefficient: string;
  hit_threshold: number;
}

export interface PromotionPlatformSettings {
  /** 5 个平台，按固定顺序；没配置过的平台是默认值。 */
  items: PromotionPlatformSetting[];
  /** 当前用户能不能改（metric_config:write）。 */
  can_edit: boolean;
  /** 本次 PUT 实际改了的平台；GET 为空。 */
  changed: string[];
  /** 本次 PUT 是否已投递博主质量标签重算。 */
  quality_recompute_queued: boolean;
}

export async function getPromotionPlatformSettings(): Promise<PromotionPlatformSettings> {
  const resp = await apiClient.get<PromotionPlatformSettings>(
    "/api/settings/promotion-platforms"
  );
  return resp.data;
}

/** 收全量 5 行。撞汇总刷新锁 → 409 REPORT_SUMMARY_REFRESH_BUSY；校验不过 → 422，loc 带行号。 */
export async function updatePromotionPlatformSettings(
  items: PromotionPlatformSetting[]
): Promise<PromotionPlatformSettings> {
  const resp = await apiClient.put<PromotionPlatformSettings>(
    "/api/settings/promotion-platforms",
    items
  );
  return resp.data;
}
