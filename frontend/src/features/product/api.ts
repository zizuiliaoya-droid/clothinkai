// U02 product feature API 调用层。

import { apiClient } from "@/services/apiClient";
import type {
  Brand,
  BrandCreate,
  BrandListResponse,
  BrandUpdate,
  CostTableFilters,
  CostTablePage,
  GoodsBrandOption,
  GoodsImage,
  MatchResponse,
  Sku,
  SkuCreate,
  SkuUpdate,
  Style,
  StyleCreate,
  StyleImageBatchResponse,
  StyleListFilters,
  StylePage,
  StyleUpdate,
} from "./types";

// ---------------------------------------------------------------------------
// 商品成本表
// ---------------------------------------------------------------------------

export async function listCostTable(
  filters: CostTableFilters = {}
): Promise<CostTablePage> {
  const resp = await apiClient.get<CostTablePage>("/api/skus/", {
    params: filters,
  });
  return resp.data;
}

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

export async function uploadStyleMainImage(
  styleId: string,
  file: File
): Promise<Style> {
  const body = new FormData();
  body.append("image", file, file.name);
  const resp = await apiClient.post<Style>(
    `/api/styles/${styleId}/main-image`,
    body
  );
  return resp.data;
}

/**
 * 按文件名 = 款号批量上传款式主图（8a-2）。一次最多 20 张，前端按 10 张一批调用。
 * 文件名只用来匹配款号（不区分大小写），对象 key 由服务端生成。
 */
export async function uploadStyleMainImagesBatch(
  files: File[]
): Promise<StyleImageBatchResponse> {
  const body = new FormData();
  for (const f of files) body.append("files", f, f.name);
  const resp = await apiClient.post<StyleImageBatchResponse>(
    "/api/styles/main-images/batch",
    body,
    // 一批最多约 3MB，服务端逐张写 R2、逐张提交，慢网下 30 秒的默认超时不够
    { timeout: 120_000 }
  );
  return resp.data;
}

export async function removeStyleMainImage(styleId: string): Promise<void> {
  await apiClient.delete(`/api/styles/${styleId}/main-image`);
}

export async function deleteStyle(styleId: string): Promise<void> {
  await apiClient.delete(`/api/styles/${styleId}`);
}

export async function disableStyle(styleId: string): Promise<Style> {
  const resp = await apiClient.post<Style>(`/api/styles/${styleId}/disable`);
  return resp.data;
}

/** 重新启用被停用的款式（is_active=true）。与 disableStyle 对称。 */
export async function enableStyle(styleId: string): Promise<Style> {
  const resp = await apiClient.post<Style>(`/api/styles/${styleId}/enable`);
  return resp.data;
}

/**
 * 恢复**软删**的款式（is_deleted=false），需要 product:delete 权限。
 * 注意：这不是「停用」的逆操作 —— 重新启用请用 {@link enableStyle}。
 */
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
// Goods（商品 / 套装）
// ---------------------------------------------------------------------------

export interface GoodsOption {
  goods_main_id: string;
  goods_code: string;
  goods_title: string;
  goods_short_name: string | null;
  is_suit: boolean;
}

/** 商品简称上限，与后端 GOODS_SHORT_NAME_MAX_LEN 一致。 */
export const GOODS_SHORT_NAME_MAX_LEN = 32;

/** 商品显示名：有简称用简称，没填回落全称。全称动辄二三十个字，列表和下拉里显示不全。 */
export function goodsDisplayName(
  goodsTitle: string | null | undefined,
  shortName: string | null | undefined
): string {
  return shortName || goodsTitle || "";
}

/** 款式归属的商品，非套装优先。返回多条说明该款既单卖又进套装，需要人工指定归属。 */
export async function listGoodsForStyle(styleId: string): Promise<GoodsOption[]> {
  const resp = await apiClient.get<GoodsOption[]>(
    `/api/styles/${styleId}/goods`
  );
  return resp.data;
}

/** 商品的成员款式。单品恰好 1 个，套装 ≥2 个。 */
export interface GoodsStyleItem {
  id: string;
  style_id: string;
  style_code: string | null;
  style_name: string | null;
  single_goods_cost: string | null;
  sort_order: number;
  is_active: boolean;
}

export interface GoodsStyleItemInput {
  style_id: string;
  single_goods_cost?: string | null;
  sort_order?: number;
}

export interface Goods {
  id: string;
  goods_code: string;
  goods_title: string;
  /** 商品简称；没填为 null，界面回落显示全称。 */
  short_name: string | null;
  // 类目已下线（8a-3）：接口不再返回 category
  season: string | null;
  brand_id: string | null;
  brand_name: string | null;
  remark: string | null;
  /**
   * 商品图由启用成员款式派生（8a-2）：按成员顺序、只含有图的成员；单品最多 1 张，
   * 套装并排、缺图不占位，都没有为 []。（main_image_key 已废弃，接口不再返回。）
   */
  images: GoodsImage[];
  is_suit: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  items: GoodsStyleItem[];
  /** 成员成本之和，任一成员缺成本时仍按已填的求和。 */
  total_cost: string | null;
  cost_missing_count: number;
  /** 挂在该商品上的平台链接数，0 表示还没上架到任何渠道。 */
  link_count: number;
  /** 给界面的提示（只在新建时有内容），如「该款已有商品「…」，已为新商品另行生成内部编码」。 */
  notices: string[];
}

export interface GoodsPage {
  items: Goods[];
  total: number;
  page: number;
  page_size: number;
}

export interface GoodsFilters {
  keyword?: string;
  season?: string;
  brand_id?: string;
  is_suit?: boolean;
  is_active?: boolean;
  include_inactive?: boolean;
  unlinked_only?: boolean;
  page?: number;
  page_size?: number;
}

export interface GoodsCreate {
  /** 不传由系统生成（补充 3）：单品 = 款号（被占用另生成），套装 = SUIT- + 成员款号组合。商品页不再传。 */
  goods_code?: string;
  goods_title: string;
  short_name?: string | null;
  season?: string | null;
  // 品牌只读（8a-4）：只由商品资料导入写入，商品接口不再收 brand_id
  remark?: string | null;
  items: GoodsStyleItemInput[];
}

/** goods_code 不在更新范围内：它是报表与链接归属的引用键，改了等于换了一个商品。 */
export interface GoodsUpdate {
  goods_title?: string;
  /** 不传不动；传 null 清掉简称。 */
  short_name?: string | null;
  season?: string | null;
  remark?: string | null;
  is_active?: boolean;
  /** 给了就整体替换成员列表；不给则不动成员。 */
  items?: GoodsStyleItemInput[];
}

export async function listGoods(filters: GoodsFilters = {}): Promise<GoodsPage> {
  const resp = await apiClient.get<GoodsPage>("/api/goods/", {
    params: filters,
  });
  return resp.data;
}

export async function getGoods(goodsId: string): Promise<Goods> {
  const resp = await apiClient.get<Goods>(`/api/goods/${goodsId}`);
  return resp.data;
}

export async function createGoods(payload: GoodsCreate): Promise<Goods> {
  const resp = await apiClient.post<Goods>("/api/goods/", payload);
  return resp.data;
}

export async function updateGoods(
  goodsId: string,
  payload: GoodsUpdate
): Promise<Goods> {
  const resp = await apiClient.put<Goods>(`/api/goods/${goodsId}`, payload);
  return resp.data;
}

export async function deleteGoods(goodsId: string): Promise<void> {
  await apiClient.delete(`/api/goods/${goodsId}`);
}

// ---------------------------------------------------------------------------
// PlatformProduct（平台链接，运维视图）
// ---------------------------------------------------------------------------

export interface PlatformLink {
  id: string;
  platform: string;
  platform_id: string;
  style_id: string;
  sku_id: string | null;
  goods_main_id: string | null;
  channel: string;
  title: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  goods_code: string | null;
  goods_title: string | null;
  goods_short_name: string | null;
  goods_is_suit: boolean;
  style_code: string | null;
  style_name: string | null;
}

export interface PlatformLinkPage {
  items: PlatformLink[];
  total: number;
  page: number;
  page_size: number;
}

export interface PlatformLinkFilters {
  style_id?: string;
  goods_main_id?: string;
  platform?: string;
  channel?: string;
  keyword?: string;
  unmapped_only?: boolean;
  page?: number;
  page_size?: number;
}

export interface PlatformLinkUpdate {
  style_id?: string;
  sku_id?: string | null;
  goods_main_id?: string;
  channel?: string;
  title?: string | null;
  is_active?: boolean;
}

export async function listPlatformLinks(
  filters: PlatformLinkFilters = {}
): Promise<PlatformLinkPage> {
  const resp = await apiClient.get<PlatformLinkPage>("/api/platform-products/", {
    params: filters,
  });
  return resp.data;
}

/** 新建平台链接（8a-5 套装「绑定链接」）。后端会去掉平台 ID 的前导单引号与首尾空白。 */
export interface PlatformLinkCreate {
  platform: string;
  platform_id: string;
  style_id: string;
  /** 归属商品；必须以 style_id 为启用成员，否则 422 INVALID_GOODS_REFERENCE。 */
  goods_main_id?: string;
  channel?: string;
  title?: string | null;
}

/**
 * POST /api/platform-products/（ops.platform_link:write）。
 * 平台 ID 已有链接 → 409 PLATFORM_PRODUCT_CONFLICT，message 里是归属商品的显示名，不覆盖。
 */
export async function createPlatformLink(
  payload: PlatformLinkCreate
): Promise<PlatformLink> {
  const resp = await apiClient.post<PlatformLink>("/api/platform-products/", payload);
  return resp.data;
}

export async function updatePlatformLink(
  id: string,
  payload: PlatformLinkUpdate
): Promise<PlatformLink> {
  const resp = await apiClient.put<PlatformLink>(
    `/api/platform-products/${id}`,
    payload
  );
  return resp.data;
}

export async function deletePlatformLink(id: string): Promise<void> {
  await apiClient.delete(`/api/platform-products/${id}`);
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

/**
 * 启用品牌（按名称），商品读权限即可（/api/brands/ 只有管理员能读）。
 * 成本表品牌筛选用（8a-4）。
 */
export async function listGoodsBrandOptions(): Promise<GoodsBrandOption[]> {
  const resp = await apiClient.get<{ items: GoodsBrandOption[] }>(
    "/api/goods/brand-options"
  );
  return resp.data.items;
}

/**
 * 商品页季节选项（8a-3）：字典 season 启用值在前，再接商品上出现过的值。
 * 查询键 ["goods", "season-options"]；字典季节增删后由 DictManagerModal 失效。
 */
export async function getGoodsSeasonOptions(): Promise<string[]> {
  const resp = await apiClient.get<{ items: string[] }>("/api/goods/season-options");
  return resp.data.items;
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

// ---------------------------------------------------------------------------
// Dict Items（可维护字典：类目/季节/颜色/尺码）
// ---------------------------------------------------------------------------

export interface DictItem {
  id: string;
  dict_type: string;
  value: string;
  sort_order: number;
  is_active: boolean;
}

export async function listDictItems(
  dictType: string
): Promise<DictItem[]> {
  const resp = await apiClient.get<DictItem[]>("/api/dict-items", {
    params: { dict_type: dictType },
  });
  return resp.data;
}

export async function createDictItem(
  dictType: string,
  value: string
): Promise<DictItem> {
  const resp = await apiClient.post<DictItem>("/api/dict-items", {
    dict_type: dictType,
    value,
  });
  return resp.data;
}

export async function deleteDictItem(itemId: string): Promise<void> {
  await apiClient.delete(`/api/dict-items/${itemId}`);
}
