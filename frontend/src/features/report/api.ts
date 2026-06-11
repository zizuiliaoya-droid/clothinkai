// report feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  ProductionReport,
  ProgressSummary,
  PrWorkProgress,
  StoreDailyRow,
  StyleCardPage,
  TargetWithActual,
} from "./types";

export async function getWorkProgress(
  month: string
): Promise<PrWorkProgress[]> {
  const resp = await apiClient.get<PrWorkProgress[]>(
    "/api/reports/work-progress",
    { params: { month } }
  );
  return resp.data;
}

export async function getTargets(month: string): Promise<TargetWithActual[]> {
  const resp = await apiClient.get<TargetWithActual[]>("/api/reports/targets", {
    params: { month },
  });
  return resp.data;
}

export async function getPublishSummary(
  params: { preset?: string; date_from?: string; date_to?: string } = {}
): Promise<ProgressSummary> {
  const resp = await apiClient.get<ProgressSummary>(
    "/api/reports/publish-progress/summary",
    { params }
  );
  return resp.data;
}

export async function getPublishCards(
  params: {
    page?: number;
    page_size?: number;
    keyword?: string;
    preset?: string;
    date_from?: string;
    date_to?: string;
  } = {}
): Promise<StyleCardPage> {
  const resp = await apiClient.get<StyleCardPage>(
    "/api/reports/publish-progress/cards",
    { params }
  );
  return resp.data;
}

export async function getStoreDaily(
  params: { preset?: string; date_from?: string; date_to?: string } = {}
): Promise<StoreDailyRow[]> {
  const resp = await apiClient.get<StoreDailyRow[]>(
    "/api/reports/store-daily",
    { params }
  );
  return resp.data;
}

export async function getProduction(
  params: {
    preset?: string;
    date_from?: string;
    date_to?: string;
    exclude_brushing?: boolean;
  } = {}
): Promise<ProductionReport> {
  const resp = await apiClient.get<ProductionReport>(
    "/api/reports/production",
    { params }
  );
  return resp.data;
}
