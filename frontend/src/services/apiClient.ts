// Axios 实例 + JWT 拦截器 + 401 自动 refresh。

import axios, { AxiosError, AxiosRequestConfig } from "axios";
import type { ApiError, TokenPair } from "@/types";
import { useSupportStore } from "@/stores/supportStore";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

const TOKEN_KEY = "clothing_erp_access_token";
const REFRESH_KEY = "clothing_erp_refresh_token";

// ---------- 本地存储 ----------

export function getAccessToken(): string | null {
  return useSupportStore.getState().session?.accessToken ?? localStorage.getItem(TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (useSupportStore.getState().session) return null;
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
  paramsSerializer: {
    // axios 默认把数组序列化成 `season[]=a&season[]=b`，FastAPI 的 `list[str]` 查询参数
    // 只认重复键 `season=a&season=b`，不配这个多选筛选会静默收不到值。
    indexes: null,
  },
});

// 请求拦截器：注入 Authorization
apiClient.interceptors.request.use((config) => {
  const session = useSupportStore.getState().session;
  (config as typeof config & { _supportKey?: number })._supportKey = session?.key ?? 0;
  if (session) {
    if (session.expiresAt <= Date.now()) {
      useSupportStore.getState().end("只读排查已到期，请重新选择客户。");
      throw new Error("只读排查已到期");
    }
    if (!["get", "head"].includes(config.method?.toLowerCase() ?? "get")) {
      throw new Error("当前为客户只读排查，不能修改客户数据");
    }
  }
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
  (response) => {
    const key = (response.config as AxiosRequestConfig & { _supportKey?: number })._supportKey;
    if (key != null && key !== (useSupportStore.getState().session?.key ?? 0)) {
      return Promise.reject(new Error("会话已切换，已丢弃上一个视图的响应"));
    }
    return response;
  },
  async (error: AxiosError<ApiError>) => {
    const original = error.config as AxiosRequestConfig & { _retry?: boolean; _supportKey?: number };
    if (original?._supportKey != null && original._supportKey !== (useSupportStore.getState().session?.key ?? 0)) {
      return Promise.reject(new Error("会话已切换，已丢弃上一个视图的响应"));
    }
    // 排查令牌永不使用平台 refresh；结束后只恢复保留的平台会话。
    if (original?._supportKey) {
      if (error.response?.status === 401 || error.response?.data?.code === "SUPPORT_SESSION_INVALID") {
        useSupportStore.getState().end("只读排查已失效，请重新选择客户。");
      }
      return Promise.reject(error);
    }

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
