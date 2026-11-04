import { create } from "zustand";
import { queryClient } from "@/services/queryClient";

export interface SupportTenant { id: string; code: string; name: string }
export interface SupportGrant {
  access_token: string;
  expires_in: number;
  tenant: SupportTenant;
}
export interface SupportSession {
  key: number;
  accessToken: string;
  expiresAt: number;
  tenant: SupportTenant;
}

let nextSessionKey = 0;
function clearCustomerQueries() {
  void queryClient.cancelQueries();
  queryClient.clear();
}

// 仅当前标签页内存保存。刷新即退出排查，持久平台登录始终不变。
export const useSupportStore = create<{
  session: SupportSession | null;
  notice: string | null;
  start: (grant: SupportGrant) => void;
  end: (notice?: string | null) => void;
  dismissNotice: () => void;
}>((set, get) => ({
  session: null,
  notice: null,
  start: (grant) => {
    clearCustomerQueries();
    set({ session: { key: ++nextSessionKey, accessToken: grant.access_token,
      expiresAt: Date.now() + grant.expires_in * 1000, tenant: grant.tenant }, notice: null });
  },
  end: (notice = null) => {
    if (get().session) clearCustomerQueries();
    set({ session: null, notice });
  },
  dismissNotice: () => set({ notice: null }),
}));

export function useReadOnlySupport() {
  return useSupportStore((state) => state.session !== null);
}
