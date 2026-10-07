// 安全模块 API（管理员网络诊断）。

import { apiClient } from "@/services/apiClient";
import type { IpDiagnostics } from "./types";

export async function getIpDiagnostics(): Promise<IpDiagnostics> {
  const resp = await apiClient.get<IpDiagnostics>("/api/security/ip-diagnostics");
  return resp.data;
}
