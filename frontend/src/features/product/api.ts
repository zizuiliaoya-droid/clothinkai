// U02 product feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  Brand,
  BrandCreate,
  BrandListResponse,
  BrandUpdate,
  MatchResponse,
  Sku,
  SkuCreate,
  SkuUpdate,
  Style,
  StyleCreate,
  StyleListFilters,
  StylePage,
  StyleUpdate,
} from "./types";

// ---------------------------------------------------------------------------
// Style
// ---------------------------------------------------------------------------

export async function listStyles(
  filters: StyleListFilters = {}
): Promise<StylePage> {
  const resp = await apiClient.get<StylePage>("/api/styles/", {
    params: filters,
  });
  return resp.data;
}

export async function getStyle(styleId: string): Promise<Style> {
  const resp = await apiClient.get<Style>(`/api/styles/${styleId}`);
  return resp.data;
}

export async function createStyle(payload: StyleCreate): Promise<Style> {
  const resp = await apiClient.post<Style>("/api/styles/", payload);
  return resp.data;
}

export async function updateStyle(
  styleId: string,
  payload: StyleUpdate
): Promise<Style> {
  const resp = await apiClient.put<Style>(`/api/styles/${styleId}`, payload);
  return resp.data;
}

export async function deleteStyle(styleId: string): Promise<void> {
  await apiClient.delete(`/api/styles/${styleId}`);
}

export async function disableStyle(styleId: string): Promise<Style> {
  const resp = await apiClient.post<Style>(`/api/styles/${styleId}/disable`);
  return resp.data;
}

export async function restoreStyle(styleId: string): Promise<Style> {
  const resp = await apiClient.post<Style>(`/api/styles/${styleId}/restore`);
  return resp.data;
}

/**
 * EP02-S06 款号 ↔ 商品简称双向关联。
 *
 * 业务未匹配 → 返回 matched=false / candidates=[] / total=0（前端允许继续手动输入）。
 * 系统失败 → axios 抛 5xx 错误，前端展示错误提示要求用户稍后重试，**不要在 UI 上显示
 * 为"未匹配"**（避免误导用户认为商品库不存在该款号）。
 */
export async function matchByCode(styleCode: string): Promise<MatchResponse> {
  const resp = await apiClient.get<MatchResponse>("/api/styles/match", {
    params: { style_code: styleCode },
  });
  return resp.data;
}

export async function matchByKeyword(
  keyword: string
): Promise<MatchResponse> {
  const resp = await apiClient.get<MatchResponse>("/api/styles/match", {
    params: { keyword },
  });
  return resp.data;
}

// ---------------------------------------------------------------------------
// Sku
// ---------------------------------------------------------------------------

export async function listSkusByStyle(
  styleId: string,
  includeInactive = false
): Promise<Sku[]> {
  const resp = await apiClient.get<Sku[]>(
    `/api/skus/by-style/${styleId}`,
    { params: { include_inactive: includeInactive } }
  );
  return resp.data;
}

export async function getSku(skuId: string): Promise<Sku> {
  const resp = await apiClient.get<Sku>(`/api/skus/${skuId}`);
  return resp.data;
}

export async function createSku(payload: SkuCreate): Promise<Sku> {
  const resp = await apiClient.post<Sku>("/api/skus/", payload);
  return resp.data;
}

export async function updateSku(
  skuId: string,
  payload: SkuUpdate
): Promise<Sku> {
  const resp = await apiClient.put<Sku>(`/api/skus/${skuId}`, payload);
  return resp.data;
}

export async function deleteSku(skuId: string): Promise<void> {
  await apiClient.delete(`/api/skus/${skuId}`);
}

// ---------------------------------------------------------------------------
// Brand
// ---------------------------------------------------------------------------

export async function listBrands(params: {
  page?: number;
  page_size?: number;
  is_active?: boolean;
} = {}): Promise<BrandListResponse> {
  const resp = await apiClient.get<BrandListResponse>("/api/brands/", {
    params,
  });
  return resp.data;
}

export async function getBrand(brandId: string): Promise<Brand> {
  const resp = await apiClient.get<Brand>(`/api/brands/${brandId}`);
  return resp.data;
}

export async function createBrand(payload: BrandCreate): Promise<Brand> {
  const resp = await apiClient.post<Brand>("/api/brands/", payload);
  return resp.data;
}

export async function updateBrand(
  brandId: string,
  payload: BrandUpdate
): Promise<Brand> {
  const resp = await apiClient.put<Brand>(`/api/brands/${brandId}`, payload);
  return resp.data;
}

export async function disableBrand(brandId: string): Promise<Brand> {
  const resp = await apiClient.delete<Brand>(`/api/brands/${brandId}`);
  return resp.data;
}
