// 千牛 / 站内推广 日报列表 API。

import { apiClient } from "@/services/apiClient";
import type {
  DataQualityIssue,
  DataQualityIssuePage,
  DataQualityResolution,
  DataQualitySeverity,
  DataQualityStatus,
  DataQualitySummaryRow,
  WorkerToken,
  WorkerTokenCreatePayload,
  WorkerTokenIssued,
} from "./types";

export interface QianniuRow {
  id: string;
  date: string;
  platform_id: string;
  visitors: number | null;
  pay_amount: string | null;
  pay_orders: number | null;
  extra: Record<string, unknown>;
}

export interface AdRow {
  id: string;
  date: string;
  platform_id: string;
  cost: string | null;
  impressions: number | null;
  clicks: number | null;
  gmv: string | null;
  extra: Record<string, unknown>;
}

export interface DailyPage<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/**
 * 日报列表查询参数。
 *
 * 除分页外还支持 date_from/date_to、platform_id 关键词、
 * 各 typed 数值列的 `<field>_min` / `<field>_max` 区间，以及 sort_by/sort_dir。
 * 字段组合随表而异（千牛 visitors/pay_amount/pay_orders，
 * 万相台 cost/impressions/clicks/gmv），故用开放的键值形态。
 */
export type DailyQueryParams = Record<string, string | number | undefined>;

export async function listQianniu(
  params: DailyQueryParams = {},
): Promise<DailyPage<QianniuRow>> {
  const resp = await apiClient.get<DailyPage<QianniuRow>>("/api/qianniu", {
    params,
  });
  return resp.data;
}

export async function listAdDaily(
  params: DailyQueryParams = {},
): Promise<DailyPage<AdRow>> {
  const resp = await apiClient.get<DailyPage<AdRow>>("/api/ad-daily", {
    params,
  });
  return resp.data;
}

const WORKER_TOKEN_PATH = "/api/crawler/worker-tokens";

export async function listWorkerTokens(): Promise<WorkerToken[]> {
  const response = await apiClient.get<WorkerToken[]>(`${WORKER_TOKEN_PATH}/`);
  return response.data;
}

export async function issueWorkerToken(
  payload: WorkerTokenCreatePayload,
): Promise<WorkerTokenIssued> {
  const response = await apiClient.post<WorkerTokenIssued>(
    `${WORKER_TOKEN_PATH}/`,
    payload,
  );
  return response.data;
}

export async function revokeWorkerToken(id: string): Promise<void> {
  await apiClient.delete(`${WORKER_TOKEN_PATH}/${id}`);
}

export async function getDataQualitySummary(): Promise<DataQualitySummaryRow[]> {
  const response = await apiClient.get<DataQualitySummaryRow[]>(
    "/api/data-quality/summary",
  );
  return response.data;
}

export async function listDataQualityIssues(
  params: {
    source?: string;
    severity?: DataQualitySeverity;
    issue_status?: DataQualityStatus;
    page?: number;
    page_size?: number;
  } = {},
): Promise<DataQualityIssuePage> {
  const response = await apiClient.get<DataQualityIssuePage>(
    "/api/data-quality/issues",
    { params },
  );
  return response.data;
}

export async function resolveDataQualityIssue(
  id: string,
  status: DataQualityResolution,
): Promise<DataQualityIssue> {
  const response = await apiClient.put<DataQualityIssue>(
    `/api/data-quality/issues/${id}`,
    { status },
  );
  return response.data;
}