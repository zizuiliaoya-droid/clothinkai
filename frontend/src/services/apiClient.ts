// Axios 实例 + JWT 拦截器 + 401 自动 refresh。

import axios, { AxiosError, AxiosRequestConfig } from "axios";
import type { ApiError, TokenPair } from "@/types";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const TOKEN_KEY = "clothing_erp_access_token";
const REFRESH_KEY = "clothing_erp_refresh_token";

// ---------- 本地存储 ----------

export function getAccessToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function setTokens(access: string, refresh: string): void {
  localStorage.setItem(TOKEN_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

// ---------- Axios 实例 ----------

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30_000,
  withCredentials: false,
});

// 请求拦截器：注入 Authorization
apiClient.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token && config.headers) {
    config.headers["Authorization"] = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截器：401 自动 refresh
let isRefreshing = false;
let pendingQueue: Array<(token: string | null) => void> = [];

function flushQueue(token: string | null): void {
  pendingQueue.forEach((cb) => cb(token));
  pendingQueue = [];
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError<ApiError>) => {
    const original = error.config as AxiosRequestConfig & { _retry?: boolean };

    // 401 + 未重试过 + 不是 login/refresh 自身
    if (
      error.response?.status === 401 &&
      !original?._retry &&
      !original?.url?.includes("/api/auth/login") &&
      !original?.url?.includes("/api/auth/refresh")
    ) {
      const refresh = getRefreshToken();
      if (!refresh) {
        clearTokens();
        return Promise.reject(error);
      }

      if (isRefreshing) {
        // 等正在进行的 refresh
        return new Promise((resolve, reject) => {
          pendingQueue.push((token) => {
            if (!token || !original) {
              reject(error);
              return;
            }
            original._retry = true;
            original.headers = {
              ...original.headers,
              Authorization: `Bearer ${token}`,
            };
            resolve(apiClient(original));
          });
        });
      }

      isRefreshing = true;
      try {
        const resp = await axios.post<TokenPair>(
          `${API_BASE_URL}/api/auth/refresh`,
          { refresh_token: refresh }
        );
        setTokens(resp.data.access_token, resp.data.refresh_token);
        flushQueue(resp.data.access_token);
        if (original) {
          original._retry = true;
          original.headers = {
            ...original.headers,
            Authorization: `Bearer ${resp.data.access_token}`,
          };
          return apiClient(original);
        }
      } catch (refreshError) {
        flushQueue(null);
        clearTokens();
        // 触发跳转登录由 authStore 监听
        window.dispatchEvent(new CustomEvent("auth:unauthorized"));
        return Promise.reject(refreshError);
      } finally {
        isRefreshing = false;
      }
    }

    return Promise.reject(error);
  }
);

// ---------- API 错误工具 ----------

export function isApiError(err: unknown): err is { response: { data: ApiError } } {
  return Boolean(
    err && typeof err === "object" && "response" in err && (err as { response?: { data?: ApiError } }).response?.data?.code
  );
}

export function extractErrorMessage(err: unknown, fallback: string = "请求失败"): string {
  if (isApiError(err)) {
    return err.response.data.message || fallback;
  }
  if (err instanceof Error) {
    return err.message || fallback;
  }
  return fallback;
}
