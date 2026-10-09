// 导入冲突的比较字段：按来源的「字段名 → 中文名」静态表（冲突页的字段筛选用，8a-6）。
// 与后端各 adapter 的 compare_field_names() 一致；后端会拒绝不在名单里的字段（422）。

// 博主（8b）：平台、质量标签只为按字段筛出旧冲突留着；报价备注只有灰豚抖音来源有（手工模版没有这列）
const BLOGGER_FIELD_LABELS: Record<string, string> = {
  nickname: "昵称",
  platform: "平台",
  wechat: "微信",
  phone: "手机号",
  follower_count: "粉丝数",
  blogger_type: "博主类型",
  gender_target: "性别投放",
  category_tags: "类目标签",
  quality_tags: "质量标签",
  quote: "报价",
  cooperation_history: "合作历史",
  remark: "备注",
  web_id: "网页ID",
  homepage_url: "主页链接",
};

export const CONFLICT_FIELD_LABELS: Record<string, Record<string, string>> = {
  manual_blogger: BLOGGER_FIELD_LABELS,
  huitun_douyin: { ...BLOGGER_FIELD_LABELS, quote_note: "报价备注" },
  // 商品资料：款式（图片）、SKU（颜色…货源类型）、商品（简称、品牌、季节）；中文名与映射目录一致
  manual_style_sku: {
    external_image_url: "图片",
    color: "颜色",
    size: "规格",
    base_price: "基本售价",
    cost_price: "成本价",
    purchase_price: "采购价",
    tag_price: "市场|吊牌价",
    sourcing_type: "货源类型",
    short_name: "商品简称",
    brand_id: "品牌",
    season: "季节",
  },
};

/** 某来源可筛选的字段（下拉选项）；没有声明的来源返回空列表。 */
export function conflictFieldOptions(
  source: string | undefined
): { value: string; label: string }[] {
  const labels = source ? CONFLICT_FIELD_LABELS[source] : undefined;
  if (!labels) return [];
  return Object.entries(labels).map(([value, label]) => ({ value, label }));
}

/** 显示冲突值：数组用「、」连接，空值显示「（空）」。 */
export function conflictValueText(
  display: string | null,
  value: unknown
): string {
  if (display != null && display !== "") return display;
  if (value == null || value === "") return "（空）";
  if (Array.isArray(value)) return value.map(String).join("、");
  return String(value);
}
