import { describe, expect, it } from "vitest";
import type { MappingSpec, MappingTarget } from "@/features/import/types";
import {
  JUSHUITAN_COLUMNS,
  groupHint,
  initialMappingValues,
  mappingColumnsToSave,
  mappingProblem,
  targetTags,
  templateColumnsFromSpec,
} from "./mappingColumns";

function target(partial: Partial<MappingTarget> & { field: string }): MappingTarget {
  return {
    label: partial.field,
    type: "str",
    default_col: partial.field,
    aliases: [],
    required: false,
    group: null,
    create_only: false,
    ...partial,
  };
}

const TARGETS: MappingTarget[] = [
  target({ field: "season", label: "季节", default_col: "季节" }),
  target({ field: "style_code", label: "款式编码", default_col: "款式编码", aliases: ["货号"], required: true }),
  target({ field: "color_size", label: "颜色及规格", default_col: "颜色及规格", group: "color_size:combined" }),
  target({ field: "color", label: "颜色", default_col: "颜色", group: "color_size:split" }),
  target({ field: "size", label: "规格", default_col: "规格", aliases: ["尺码"], group: "color_size:split" }),
  target({ field: "style_name", label: "商品名称", default_col: "商品名称", required: true, create_only: true }),
  target({ field: "cost_price", label: "成本价", default_col: "成本价", type: "decimal" }),
];

function spec(active: MappingSpec["active"] = null): MappingSpec {
  return { source: "manual_style_sku", targets: TARGETS, builtin_columns: [], active };
}

describe("templateColumnsFromSpec", () => {
  it("内置默认：列默认列与别名，必填在前、其一必填其次", () => {
    expect(templateColumnsFromSpec(spec())).toEqual([
      "款式编码", "货号", "商品名称", "颜色及规格", "颜色", "规格", "尺码", "季节", "成本价",
    ]);
  });

  it("有生效的自定义映射：只列它读的列", () => {
    const cols = templateColumnsFromSpec(
      spec({
        version: 2,
        created_by_name: "跟单",
        created_at: "2026-10-07T00:00:00Z",
        columns: [
          { source_col: "进价", target_field: "cost_price" },
          { source_col: "款号", target_field: "style_code" },
        ],
      })
    );
    expect(cols).toEqual(["款号", "进价"]);
  });

  it("没取到目录时为空（组件不显示列说明）", () => {
    expect(templateColumnsFromSpec(undefined)).toEqual([]);
  });
});

describe("映射抽屉", () => {
  it("初始值：内置默认读默认列", () => {
    expect(initialMappingValues(spec()).style_code).toBe("款式编码");
  });

  it("标注与组说明", () => {
    expect(targetTags(TARGETS[5])).toEqual(["必填", "仅新建时写入"]);
    expect(targetTags(TARGETS[3])).toEqual(["其一必填"]);
    expect(groupHint(TARGETS)).toBe("颜色及规格，或 颜色 + 规格");
  });

  it("本地检查：必填与其一必填", () => {
    const base = { style_code: "款号", style_name: "商品名称" };
    expect(mappingProblem(TARGETS, { ...base, color_size: "颜色及规格" })).toBeNull();
    expect(mappingProblem(TARGETS, { ...base, color: "颜色", size: "规格" })).toBeNull();
    expect(mappingProblem(TARGETS, { ...base, color: "颜色" })).toContain("至少要填一组");
    expect(mappingProblem(TARGETS, { style_name: "x", color_size: "y" })).toContain("款式编码");
  });

  it("保存只收填了列名的字段，类型照目录", () => {
    expect(
      mappingColumnsToSave(TARGETS, { style_code: " 款号 ", cost_price: "进价", season: "" })
    ).toEqual([
      { source_col: "款号", target_field: "style_code", type: "str" },
      { source_col: "进价", target_field: "cost_price", type: "decimal" },
    ]);
  });

  it("候选列是聚水潭 42 列", () => {
    expect(JUSHUITAN_COLUMNS).toHaveLength(42);
    expect(new Set(JUSHUITAN_COLUMNS).size).toBe(42);
  });
});
