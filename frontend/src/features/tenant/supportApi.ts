import { apiClient } from "@/services/apiClient";
import type { SupportGrant } from "@/stores/supportStore";

export async function createSupportSession(tenantId: string): Promise<SupportGrant> {
  return (await apiClient.post<SupportGrant>("/api/platform/support-sessions", { tenant_id: tenantId })).data;
}
