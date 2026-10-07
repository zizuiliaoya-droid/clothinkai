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

/** GET /api/goods/brand-options 的一项：启用品牌（商品读权限即可取，8a-4）。 */
export interface GoodsBrandOption {
  id: string;
  brand_name: string;
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

/** 款式图来源：upload = 已上传主图，external = 聚水潭外部链接。 */
export type ImageSource = "upload" | "external";

/** 商品图的一张（由成员款式派生，8a-2）。 */
export interface GoodsImage {
  style_id: string;
  style_code: string;
  url: string;
  source: ImageSource;
}

export type StyleImageBatchStatus =
  | "created"
  | "replaced"
  | "unmatched"
  | "rejected"
  | "failed";

/** POST /api/styles/main-images/batch 结果里的一项。 */
export interface StyleImageBatchItem {
  filename: string;
  stem: string;
  status: StyleImageBatchStatus;
  style_id?: string | null;
  style_code?: string | null;
  reason?: string | null;
}

export interface StyleImageBatchSummary {
  created: number;
  replaced: number;
  unmatched: number;
  rejected: number;
  failed: number;
}

export interface StyleImageBatchResponse {
  results: StyleImageBatchItem[];
  summary: StyleImageBatchSummary;
}

export interface Style {
  id: string;
  style_code: string;
  style_name: string;
  short_name: string | null;
  /** 千牛商品ID 属于平台链接层，只在运维视图维护，款式维护处不展示。 */
  qianniu_product_id?: string | null;
  /** 所属商品（后端派生）：主商品为非套装优先、货号次之。 */
  goods_code: string | null;
  goods_title: string | null;
  goods_short_name: string | null;
  goods_is_suit: boolean;
  /** 所属套装的显示名（后端派生，有简称用简称）；不在任何套装里则为 null。 */
  suite_name: string | null;
  /** 简称 / 品牌 / 类目 / 季节：只读存量值，款式表单不再维护（8a）；新建的款式类目为 null。 */
  brand_id: string | null;
  category: string | null;
  season: string | null;
  gender: string | null;
  tags: string[];
  tag_color: string[];
  main_image_key: string | null;
  /** 已上传主图的签名 URL；展示请用 image_url。 */
  main_image_url: string | null;
  /** 聚水潭「图片」列导入的外部链接（只经导入与冲突裁决写入，界面不可编辑）。 */
  external_image_url: string | null;
  /** 款式图（8a-2）：已上传主图签名 URL > 外部链接 > null。 */
  image_url: string | null;
  image_source: ImageSource | null;
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
  qianniu_product_id?: string | null;
  gender?: Gender | null;
  tags?: string[];
  tag_color?: string[];
  main_image_key?: string | null;
  remark?: string | null;
  owner_id?: string | null;
  design_status?: DesignStatus;
}

export interface StyleUpdate {
  /** 款号建档后不可改：传与现值不同的值后端返回 422 STYLE_CODE_IMMUTABLE。 */
  style_code?: string;
  style_name?: string;
  qianniu_product_id?: string | null;
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
 * 字段可见性由后端字段级权限矩阵过滤；无权角色收到 null。
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
  tag_price: string | null;
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
  tag_price?: string | null;
  sourcing_type?: SourcingType;
}

export interface SkuUpdate {
  sku_code?: string;
  color?: string;
  size?: string;
  cost_price?: string | null;
  purchase_price?: string | null;
  base_price?: string | null;
  tag_price?: string | null;
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
  /** 款式主图的 R2 key，不能直接当 src（私有桶）；展示用 image_url。 */
  image_key: string | null;
  /** 款式图（8a-2）：已上传主图签名 URL > 外部链接 > null。 */
  image_url: string | null;
  image_source: ImageSource | null;
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
  /** 采购方式。决定成本价/采购价哪个必填（BR-U02-13）。 */
  sourcing_type: string;
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
