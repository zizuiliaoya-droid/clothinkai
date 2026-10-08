// 成本表「商品资料导入」的列说明与字段映射抽屉用的纯函数（8a-4，设计 §6.2、§4.7）。

import type {
  FieldMappingColumn,
  MappingSpec,
  MappingTarget,
} from "@/features/import/types";

/** 聚水潭商品资料导出的 42 列表头（设计 §1.2），映射抽屉「读哪一列」的候选。 */
export const JUSHUITAN_COLUMNS: readonly string[] = [
  "图片", "款式编码", "商品编码", "商品名称", "商品简称", "颜色及规格", "颜色", "规格",
  "基本售价", "其它属性1", "其它属性2", "其它属性3", "成本价", "采购价", "市场|吊牌价",
  "品牌", "分类", "虚拟分类", "商品标签", "国标码", "供应商名称", "重量", "长", "宽", "高",
  "体积", "单位", "商品状态", "库存同步", "备注", "库容下限", "库容上限", "溢出数量",
  "标准装箱数量", "标准装箱体积", "主仓位", "其它价格1", "其它价格2", "其它价格3",
  "修改时间", "创建时间", "创建人",
];

/** 必填（含「其一必填」组）的目标字段排在前面，组内保持目录顺序。 */
function requiredFirst(targets: MappingTarget[]): MappingTarget[] {
  const rank = (t: MappingTarget) => (t.required ? 0 : t.group ? 1 : 2);
  return [...targets].sort((a, b) => rank(a) - rank(b));
}

/**
 * 列说明里列出的列名：有生效的自定义映射时列它读的列，否则列目录的默认列与别名；必填在前、去重。
 */
export function templateColumnsFromSpec(spec: MappingSpec | undefined): string[] {
  if (!spec) return [];
  const ordered = requiredFirst(spec.targets);
  const names: string[] = [];
  if (spec.active) {
    const byField = new Map(spec.active.columns.map((c) => [c.target_field, c.source_col]));
    for (const t of ordered) {
      const col = byField.get(t.field);
      if (col) names.push(col);
    }
  } else {
    for (const t of ordered) names.push(t.default_col, ...t.aliases);
  }
  return [...new Set(names)];
}

/** 抽屉的初始值：目标字段 → 读哪一列（自定义映射读它的 source_col，否则默认列）。 */
export function initialMappingValues(spec: MappingSpec): Record<string, string> {
  const out: Record<string, string> = {};
  if (spec.active) {
    for (const c of spec.active.columns) out[c.target_field] = c.source_col;
    return out;
  }
  for (const t of spec.targets) out[t.field] = t.default_col;
  return out;
}

/** 目标字段的标注：必填 / 其一必填 / 仅新建时写入。 */
export function targetTags(target: MappingTarget): string[] {
  const tags: string[] = [];
  if (target.required) tags.push("必填");
  if (target.group) tags.push("其一必填");
  if (target.create_only) tags.push("仅新建时写入");
  return tags;
}

/** 「其一必填」组的说明文字，如「颜色及规格，或 颜色 + 规格」。 */
export function groupHint(targets: MappingTarget[]): string | null {
  const options = new Map<string, MappingTarget[]>();
  for (const t of targets) {
    if (!t.group) continue;
    const list = options.get(t.group) ?? [];
    list.push(t);
    options.set(t.group, list);
  }
  if (options.size === 0) return null;
  return [...options.values()]
    .map((members) => members.map((m) => m.label).join(" + "))
    .join("，或 ");
}

/**
 * 保存前的本地检查（后端还会再校验一次）：返回错误文案，没问题返回 null。
 * 必填都要填；「其一必填」组至少有一个选项的字段全部填了。
 */
export function mappingProblem(
  targets: MappingTarget[],
  values: Record<string, string>
): string | null {
  const filled = (field: string) => Boolean(values[field]?.trim());
  for (const t of targets) {
    if (t.required && !filled(t.field)) return `请填写「${t.label}」读哪一列`;
  }
  const groups = new Map<string, Map<string, MappingTarget[]>>();
  for (const t of targets) {
    if (!t.group) continue;
    const [name, option = ""] = t.group.split(":");
    const byOption = groups.get(name) ?? new Map<string, MappingTarget[]>();
    byOption.set(option, [...(byOption.get(option) ?? []), t]);
    groups.set(name, byOption);
  }
  for (const byOption of groups.values()) {
    const ok = [...byOption.values()].some((members) => members.every((m) => filled(m.field)));
    if (!ok) {
      const text = [...byOption.values()]
        .map((members) => members.map((m) => m.label).join(" + "))
        .join("，或 ");
      return `至少要填一组：${text}`;
    }
  }
  return null;
}

/** 保存用的列：只收填了列名的目标字段；类型由后端按目录定，这里照目录带上。 */
export function mappingColumnsToSave(
  targets: MappingTarget[],
  values: Record<string, string>
): FieldMappingColumn[] {
  return targets
    .filter((t) => values[t.field]?.trim())
    .map((t) => ({
      source_col: values[t.field].trim(),
      target_field: t.field,
      type: t.type as FieldMappingColumn["type"],
    }));
}
