// 导入冲突的比较字段：按来源的「字段名 → 中文名」静态表（冲突页的字段筛选用，8a-6）。
// 与后端各 adapter 的 compare_field_names() 一致；后端会拒绝不在名单里的字段（422）。

export const CONFLICT_FIELD_LABELS: Record<string, Record<string, string>> = {
  manual_blogger: {
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
  },
  // 商品资料（款式 / SKU / 商品）的比较字段随 8a-4 补齐
  manual_style_sku: {},
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
