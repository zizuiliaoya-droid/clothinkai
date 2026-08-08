export interface WorkerToken {
  id: string;
  name: string;
  ip_allowlist: string[];
  is_active: boolean;
  consecutive_auth_failures: number;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkerTokenIssued extends WorkerToken {
  token: string;
}

export interface WorkerTokenCreatePayload {
  name: string;
  ip_allowlist: string[];
}

export type DataQualitySeverity = "info" | "warning" | "error";
export type DataQualityStatus = "open" | "fixed" | "ignored";
export type DataQualityResolution = Exclude<DataQualityStatus, "open">;

export interface DataQualityIssue {
  id: string;
  source: string;
  severity: DataQualitySeverity;
  status: DataQualityStatus;
  entity_type: string | null;
  entity_ref: string | null;
  message: string;
  created_at: string;
}

export interface DataQualityIssuePage {
  items: DataQualityIssue[];
  total: number;
  page: number;
  page_size: number;
}

export interface DataQualitySummaryRow {
  source: string;
  severity: DataQualitySeverity;
  count: number;
}
