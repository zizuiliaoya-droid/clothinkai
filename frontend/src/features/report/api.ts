// report feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  BiDashboard,
  BiGoodsNotes,
  DrillPromotionPage,
  DrillPromotionParams,
  ProductionReport,
  ProductionTrend,
  ProgressSummary,
  PrWorkProgress,
  ReportCapabilities,
  ReportFreshness,
  StoreDailyManualResult,
  StoreGranularity,
  StoreReport,
  StyleCardPage,
  SummaryRefreshResult,
  TargetWithActual,
  TimeGranularity,
  WorkProgressGoodsPage,
  WorkProgressWeeks,
} from "./types";
import type { StoreManualPatch } from "./store/storeManual";

/** 工作进度三个接口共用的筛选：区间 + 季节多选（重复键，7e R5）。 */
export interface WorkProgressParams {
  preset?: string;
  date_from?: string;
  date_to?: string;
  season?: string[];
}

/** 工作进度按区间统计（7e R1；R5 加季节）；只持 read_own 时后端只返回本人那一行。 */
export async function getWorkProgress(
  params: WorkProgressParams = {}
): Promise<PrWorkProgress[]> {
  const resp = await apiClient.get<PrWorkProgress[]>(
    "/api/reports/work-progress",
    { params }
  );
  return resp.data;
}

/** 工作进度单品卡片 / 「查看全部」（7e R5）：limit 1 ~ 100，offset ≥ 0。 */
export async function getWorkProgressGoods(
  params: WorkProgressParams & { limit?: number; offset?: number } = {}
): Promise<WorkProgressGoodsPage> {
  const resp = await apiClient.get<WorkProgressGoodsPage>(
    "/api/reports/work-progress/goods",
    { params }
  );
  return resp.data;
}

/** 工作进度周明细（7e R5）：ISO 周分桶，首尾周截到区间。 */
export async function getWorkProgressWeeks(
  params: WorkProgressParams = {}
): Promise<WorkProgressWeeks> {
  const resp = await apiClient.get<WorkProgressWeeks>(
    "/api/reports/work-progress/weeks",
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

/**
 * 店铺日手填（7e R4）：只发改了的字段，null = 清空；要 report.store_daily:write。
 * 晚于今天 → 422 STORE_DAILY_FUTURE_DATE，金额不合法 → 422，提示在 message 里。
 */
export async function updateStoreDaily(
  day: string,
  patch: StoreManualPatch
): Promise<StoreDailyManualResult> {
  const resp = await apiClient.put<StoreDailyManualResult>(
    `/api/reports/store-daily/${encodeURIComponent(day)}`,
    patch
  );
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
 * 投产或工作进度（含只看本人）读权限即可（7e R5 放宽）；查询键 ["reports", "season-options"]。
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
    /** 单品块与单品趋势的 ROI 是否扣刷单（7e R6，后端默认 true）；店铺块是全店口径、不跟它。 */
    exclude_brushing?: boolean;
  } = {}
): Promise<BiDashboard> {
  const resp = await apiClient.get<BiDashboard>("/api/reports/bi", { params });
  return resp.data;
}

/**
 * BI Tab2 单品趋势（7e R6）：参数与响应同 `/production/trend`，判权挂 `report.bi:read`（财务也能调）。
 * 查询键 ["bi-goods", "trend", …]（刷新汇总后随 `bi-goods` 一起作废）。
 */
export async function getBiGoodsTrend(
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
    `/api/reports/bi/goods/${encodeURIComponent(goodsId)}/trend`,
    { params }
  );
  return resp.data;
}

/** BI Tab2 笔记散点（7e R6）：起止必填、跨度 ≤ 366 天；查询键 ["bi-goods", "notes", …]。 */
export async function getBiGoodsNotes(
  goodsId: string,
  params: { date_from: string; date_to: string }
): Promise<BiGoodsNotes> {
  const resp = await apiClient.get<BiGoodsNotes>(
    `/api/reports/bi/goods/${encodeURIComponent(goodsId)}/notes`,
    { params }
  );
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
