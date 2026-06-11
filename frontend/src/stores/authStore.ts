// 全局认证状态（Zustand）。

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { UserSummary } from "@/types";
import { clearTokens, setTokens } from "@/services/apiClient";

interface AuthState {
  user: UserSummary | null;
  isAuthenticated: boolean;
  mustChangePassword: boolean;
  setSession: (
    user: UserSummary,
    accessToken: string,
    refreshToken: string,
    mustChangePassword: boolean
  ) => void;
  setUser: (user: UserSummary) => void;
  logout: () => void;
  hasRole: (code: string) => boolean;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      isAuthenticated: false,
      mustChangePassword: false,

      setSession: (user, accessToken, refreshToken, mustChangePassword) => {
        setTokens(accessToken, refreshToken);
        set({
          user,
          isAuthenticated: true,
          mustChangePassword,
        });
      },

      setUser: (user) => {
        set({ user });
      },

      logout: () => {
        clearTokens();
        set({
          user: null,
          isAuthenticated: false,
          mustChangePassword: false,
        });
      },

      hasRole: (code) => {
        return get().user?.roles.includes(code) ?? false;
      },
    }),
    {
      name: "clothing_erp_auth",
      partialize: (state) => ({
        user: state.user,
        isAuthenticated: state.isAuthenticated,
        mustChangePassword: state.mustChangePassword,
      }),
    }
  )
);
