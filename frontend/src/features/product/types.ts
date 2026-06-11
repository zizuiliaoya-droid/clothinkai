// U02 product feature 类型定义。

export type Category =
  | "连衣裙"
  | "上衣"
  | "裤装"
  | "裙装"
  | "外套"
  | "套装"
  | "配饰";

export type Season = "春" | "夏" | "秋" | "冬" | "四季";
export type Gender = "女" | "男" | "中性" | "童";
export type DesignStatus = "设计中" | "大货";
export type SourcingType = "自产" | "外采" | "混合";

// ---------------------------------------------------------------------------
// Brand
// ---------------------------------------------------------------------------

export interface Brand {
  id: string;
  brand_code: string;
  brand_name: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface BrandCreate {
  brand_code: string;
  brand_name: string;
}

export interface BrandUpdate {
  brand_name?: string;
  is_active?: boolean;
}

// ---------------------------------------------------------------------------
// Style
// ---------------------------------------------------------------------------

export interface Style {
  id: string;
  style_code: string;
  style_name: string;
  short_name: string | null;
  brand_id: string | null;
  category: string;
  season: string | null;
  gender: string | null;
  tags: string[];
  tag_color: string[];
  main_image_key: string | null;
  main_image_url: string | null;
  remark: string | null;
  owner_id: string | null;
  design_status: string;
  is_active: boolean;
  is_deleted: boolean;
  created_at: string;
  updated_at: string;
}

export interface StyleCreate {
  style_code: string;
  style_name: string;
  short_name?: string | null;
  brand_id?: string | null;
  category: Category;
  season?: Season | null;
  gender?: Gender | null;
  tags?: string[];
  tag_color?: string[];
  main_image_key?: string | null;
  remark?: string | null;
  owner_id?: string | null;
  design_status?: DesignStatus;
}

export interface StyleUpdate {
  style_code?: string;
  style_name?: string;
  short_name?: string | null;
  brand_id?: string | null;
  category?: Category;
  season?: Season | null;
  gender?: Gender | null;
  tags?: string[];
  tag_color?: string[];
  main_image_key?: string | null;
  remark?: string | null;
  owner_id?: string | null;
  design_status?: DesignStatus;
  is_active?: boolean;
}

export interface StylePage {
  items: Style[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// Sku
// ---------------------------------------------------------------------------

/**
 * SKU 响应。
 *
 * 注意：cost_price / purchase_price 由后端按角色过滤；
 * 当前角色（PR / 设计师等）不可见时返回 null（PR 在 UI 上隐藏价格列即可）。
 *
 * TODO U09: 改为基于服务端字段级权限矩阵的字段过滤。
 */
export interface Sku {
  id: string;
  style_id: string;
  sku_code: string;
  color: string;
  size: string;
  cost_price: string | null;
  purchase_price: string | null;
  base_price: string | null;
  sourcing_type: string;
  is_active: boolean;
  is_deleted: boolean;
  created_at: string;
  updated_at: string;
}

export interface SkuCreate {
  style_id: string;
  sku_code: string;
  color: string;
  size: string;
  cost_price?: string | null;
  purchase_price?: string | null;
  base_price?: string | null;
  sourcing_type?: SourcingType;
}

export interface SkuUpdate {
  sku_code?: string;
  color?: string;
  size?: string;
  cost_price?: string | null;
  purchase_price?: string | null;
  base_price?: string | null;
  sourcing_type?: SourcingType;
  is_active?: boolean;
}

// ---------------------------------------------------------------------------
// Match (款号 ↔ 商品简称双向关联)
// ---------------------------------------------------------------------------

export interface MatchCandidate {
  id: string;
  style_code: string;
  style_name: string;
  short_name: string | null;
  display_short_name: string;
}

export interface MatchResponse {
  matched: boolean;
  candidates: MatchCandidate[];
  total: number;
}

// ---------------------------------------------------------------------------
// 列表筛选
// ---------------------------------------------------------------------------

export interface StyleListFilters {
  page?: number;
  page_size?: number;
  keyword?: string;
  brand_id?: string;
  category?: string;
  season?: string;
  gender?: string;
  design_status?: string;
  is_active?: boolean;
  include_inactive?: boolean;
}

export interface BrandListResponse {
  items: Brand[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// 商品成本表（SKU 级 join 款式+品牌）— 对齐 final.xlsx 13 列
// ---------------------------------------------------------------------------

export interface CostTableRow {
  sku_id: string;
  style_id: string;
  image_key: string | null;
  style_code: string;
  sku_code: string;
  style_name: string;
  short_name: string | null;
  color_size: string;
  color: string;
  size: string;
  base_price: string | null;
  cost_price: string | null;
  purchase_price: string | null;
  tag_price: string | null;
  brand_name: string | null;
  is_active: boolean;
}

export interface CostTablePage {
  items: CostTableRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface CostTableFilters {
  page?: number;
  page_size?: number;
  keyword?: string;
  brand_id?: string;
  include_inactive?: boolean;
}
