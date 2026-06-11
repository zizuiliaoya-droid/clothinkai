// 共享 TypeScript 类型定义。

export interface UserSummary {
  id: string;
  username: string;
  display_name: string | null;
  email: string | null;
  status: string;
  password_must_change?: boolean;
  roles: string[];
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  must_change_password: boolean;
  expires_in: number;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface ChangePasswordRequest {
  old_password: string;
  new_password: string;
}

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface PageMeta {
  page: number;
  page_size: number;
  total: number;
}
