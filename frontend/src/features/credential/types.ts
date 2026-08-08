export const CREDENTIAL_PLATFORMS = ["千牛", "万相台", "灰豚"] as const;
export type CredentialPlatform = (typeof CREDENTIAL_PLATFORMS)[number];

export type CredentialStatus = "active" | "paused" | "disabled";

export interface Credential {
  id: string;
  platform: CredentialPlatform;
  username: string;
  status: CredentialStatus;
  consecutive_failures: number;
  last_failure_reason: string | null;
  last_failure_at: string | null;
  privacy_consent_at: string;
  remark: string | null;
  created_at: string;
  updated_at: string;
}

export interface CredentialPage {
  items: Credential[];
  total: number;
  page: number;
  page_size: number;
}

export interface CredentialListParams {
  platform?: CredentialPlatform;
  cred_status?: CredentialStatus;
  page?: number;
  page_size?: number;
}

export interface CredentialCreatePayload {
  platform: CredentialPlatform;
  username: string;
  password: string;
  privacy_consent: boolean;
  remark?: string | null;
}

export interface CredentialUpdatePayload {
  password?: string;
  remark?: string | null;
}
