// report feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  BiDashboard,
  DrillPromotionPage,
  DrillPromotionParams,
  ProductionReport,
  ProductionTrend,
  ProgressSummary,
  PrWorkProgress,
  ReportCapabilities,
  ReportFreshness,
  StoreGranularity,
  StoreReport,
  StyleCardPage,
  SummaryRefreshResult,
  TargetWithActual,
  TimeGranularity,
} from "./types";

/** 工作进度按区间统计（7e R1）；只持 read_own 时后端只返回本人那一行。 */
export async function getWorkProgress(
  params: { preset?: string; date_from?: string; date_to?: string } = {}
): Promise<PrWorkProgress[]> {
  const resp = await apiClient.get<PrWorkProgress[]>(
    "/api/reports/work-progress",
    { params }
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

const STORE_REPORT_PATHS: Record<StoreGranularity, string> = {
  day: "/api/reports/store/daily",
  week: "/api/reports/store/weekly",
  month: "/api/reports/store/monthly",
};

/** 店铺日明细 / 周汇总 / 月度汇总（7e R4）：后端按「ISO 周（自然月）∩ 区间」分桶，行上带实际起止。 */
export async function getStoreReport(
  granularity: StoreGranularity,
  params: { preset?: string; date_from?: string; date_to?: string } = {}
): Promise<StoreReport> {
  const resp = await apiClient.get<StoreReport>(STORE_REPORT_PATHS[granularity], { params });
  return resp.data;
}

export async function getProduction(
  params: {
    preset?: string;
    date_from?: string;
    date_to?: string;
    exclude_brushing?: boolean;
    /** 季节多选（PRD 第 4 章）。axios 已配 indexes:null，会序列化成重复键。 */
    season?: string[];
    // 类目筛选已下线（8a-3）
  } = {}
): Promise<ProductionReport> {
  const resp = await apiClient.get<ProductionReport>(
    "/api/reports/production",
    { params }
  );
  return resp.data;
}

/**
 * 投产页季节选项（8a-3，C-02）：字典 season 启用值 + 商品上出现过的值。
 * 只要投产报表读权限（主管也能拿到）；查询键 ["reports", "season-options"]。
 */
export async function getReportSeasonOptions(): Promise<string[]> {
  const resp = await apiClient.get<{ items: string[] }>("/api/reports/season-options");
  return resp.data.items;
}

export async function getProductionTrend(
  goodsId: string,
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
    { params: { goods_id: goodsId, ...params } }
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

export async function getSummaryFreshness(
  params: { preset?: string; date_from?: string; date_to?: string } = {}
): Promise<ReportFreshness> {
  const resp = await apiClient.get<ReportFreshness>(
    "/api/reports/summaries/freshness",
    { params }
  );
  return resp.data;
}

export async function refreshSummaries(
  dateFrom: string,
  dateTo: string
): Promise<SummaryRefreshResult> {
  const resp = await apiClient.post<SummaryRefreshResult>(
    "/api/reports/summaries/refresh",
    null,
    {
      params: { date_from: dateFrom, date_to: dateTo },
      // 区间最长 366 天，要逐日重算工作进度，给足时间（默认 30 秒）
      timeout: 120_000,
    }
  );
  return resp.data;
}

/** 报表能力（7e R1）：菜单与导出按钮按它显示；只要登录。 */
export async function getReportCapabilities(): Promise<ReportCapabilities> {
  const resp = await apiClient.get<ReportCapabilities>("/api/reports/capabilities");
  return resp.data;
}

/**
 * 报表下钻：区间内（按合作日期）的推广单明细（7e R1）。只持工作进度 read_own 时后端强制本人。
 */
export async function getDrillPromotions(
  params: DrillPromotionParams
): Promise<DrillPromotionPage> {
  const resp = await apiClient.get<DrillPromotionPage>("/api/reports/drill/promotions", {
    params,
  });
  return resp.data;
}

export type ReportExportType = "work-progress" | "store-daily" | "production";

export interface ReportExportParams {
  preset?: string;
  date_from?: string;
  date_to?: string;
  exclude_brushing?: boolean;
  season?: string[];
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
