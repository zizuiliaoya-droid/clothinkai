// 千牛 / 站内推广 日报列表 API。

import { apiClient } from "@/services/apiClient";

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

export async function listQianniu(params: {
  page?: number;
  page_size?: number;
  date_from?: string;
  date_to?: string;
} = {}): Promise<DailyPage<QianniuRow>> {
  const resp = await apiClient.get<DailyPage<QianniuRow>>("/api/qianniu", {
    params,
  });
  return resp.data;
}

export async function listAdDaily(params: {
  page?: number;
  page_size?: number;
  date_from?: string;
  date_to?: string;
} = {}): Promise<DailyPage<AdRow>> {
  const resp = await apiClient.get<DailyPage<AdRow>>("/api/ad-daily", {
    params,
  });
  return resp.data;
}
