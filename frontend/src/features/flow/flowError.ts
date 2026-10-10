// 流程动作失败时界面怎么处理（流程线设计 8.2）。纯函数，vitest 测；弹窗按返回的 kind 决定标红、提示还是刷新。
//
// - 422 FLOW_GATE_MISSING → gate：missing 交给 missingToFields 标红，弹窗不关
// - 403 FLOW_ACTION_FORBIDDEN → 直接显示 reason
// - 403 PERMISSION_DENIED（缺端点 scope：权限刚被改、页面还是旧的）→ 通用文案 + 刷新列表与详情（重新拿 ui）
// - 409（这张单刚被别人处理过）→ 统一文案 + 刷新
// - 其余 → 接口的 message

import { extractErrorMessage } from "@/services/apiClient";
import type { GateMissingItem } from "./keys";
import { extractGateMissing } from "./missingToFields";

export const PERMISSION_REFRESH_TEXT = "没有这个操作的权限，已刷新页面权限";
export const CONFLICT_REFRESH_TEXT = "这张单刚被别人处理过，已刷新";

export type FlowErrorOutcome =
  | { kind: "gate"; missing: GateMissingItem[] }
  | { kind: "message"; text: string; refresh: boolean };

interface ErrorShape {
  response?: { status?: number; data?: { code?: unknown; message?: unknown; details?: unknown } };
}

export function flowErrorOutcome(err: unknown): FlowErrorOutcome {
  const missing = extractGateMissing(err);
  if (missing) return { kind: "gate", missing };

  const resp = (err as ErrorShape | null | undefined)?.response;
  const code = resp?.data?.code;
  if (resp?.status === 409) return { kind: "message", text: CONFLICT_REFRESH_TEXT, refresh: true };
  if (code === "PERMISSION_DENIED") {
    return { kind: "message", text: PERMISSION_REFRESH_TEXT, refresh: true };
  }
  if (code === "FLOW_ACTION_FORBIDDEN") {
    const reason = (resp?.data?.details as { reason?: unknown } | undefined)?.reason;
    if (typeof reason === "string" && reason) return { kind: "message", text: reason, refresh: false };
  }
  return { kind: "message", text: extractErrorMessage(err), refresh: false };
}
