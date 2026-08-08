// report feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  BiDashboard,
  ProductionReport,
  ProductionTrend,
  ProgressSummary,
  PrWorkProgress,
  StoreDailyRow,
  StyleCardPage,
  TargetWithActual,
  TimeGranularity,
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
    season?: string;
  } = {}
): Promise<ProductionReport> {
  const resp = await apiClient.get<ProductionReport>(
    "/api/reports/production",
    { params }
  );
  return resp.data;
}

export async function getProductionTrend(
  styleId: string,
  params: {
    preset?: string;
    date_from?: string;
    date_to?: string;
    granularity?: TimeGranularity;
    exclude_brushing?: boolean;
  } = {}
): Promise<ProductionTrend> {
  const resp = await apiClient.get<ProductionTrend>(
    "/api/reports/production/trend",
    { params: { style_id: styleId, ...params } }
  );
  return resp.data;
}

export async function getBiDashboard(
  params: {
    preset?: string;
    date_from?: string;
    date_to?: string;
    granularity?: TimeGranularity;
  } = {}
): Promise<BiDashboard> {
  const resp = await apiClient.get<BiDashboard>("/api/reports/bi", { params });
  return resp.data;
}

export type ReportExportType = "work-progress" | "store-daily" | "production";

export interface ReportExportParams {
  preset?: string;
  date_from?: string;
  date_to?: string;
  exclude_brushing?: boolean;
  season?: string;
  granularity?: TimeGranularity;
}

function exportFilename(disposition: string | undefined, fallback: string): string {
  if (!disposition) return fallback;
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded.replace(/^"|"$/g, ""));
    } catch {
      return encoded;
    }
  }
  return disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? fallback;
}

async function normalizeExportError(error: unknown): Promise<never> {
  const response = (error as { response?: { data?: unknown } } | null)?.response;
  if (response?.data instanceof Blob) {
    const text = await response.data.text();
    try {
      const payload = JSON.parse(text) as { message?: string; detail?: string };
      throw new Error(payload.message ?? payload.detail ?? "导出失败");
    } catch (parseError) {
      if (parseError instanceof SyntaxError) {
        throw new Error(text || "导出失败");
      }
      throw parseError;
    }
  }
  throw error;
}

export async function exportReport(
  type: ReportExportType,
  params: ReportExportParams = {},
): Promise<string> {
  try {
    const response = await apiClient.get<Blob>(`/api/reports/${type}/export`, {
      params,
      responseType: "blob",
    });
    const filename = exportFilename(
      response.headers["content-disposition"],
      `${type}.xlsx`,
    );
    const url = URL.createObjectURL(response.data);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    return filename;
  } catch (error) {
    return normalizeExportError(error);
  }
}
