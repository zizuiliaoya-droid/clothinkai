import { describe, expect, it } from "vitest";
import {
  CONFLICT_FIELD_LABELS,
  conflictFieldOptions,
  conflictValueText,
} from "./conflictFields";

describe("conflictFieldOptions", () => {
  it("博主来源列出全部比较字段（与后端 compare_field_names 一致）", () => {
    const values = conflictFieldOptions("manual_blogger").map((o) => o.value);
    expect(values.sort()).toEqual(
      [
        "nickname",
        "platform",
        "wechat",
        "phone",
        "follower_count",
        "blogger_type",
        "gender_target",
        "category_tags",
        "quality_tags",
        "quote",
        "cooperation_history",
        "remark",
        "web_id",
        "homepage_url",
      ].sort()
    );
    expect(CONFLICT_FIELD_LABELS.manual_blogger.quote).toBe("报价");
    expect(CONFLICT_FIELD_LABELS.manual_blogger.web_id).toBe("网页ID");
    expect(CONFLICT_FIELD_LABELS.manual_blogger.homepage_url).toBe("主页链接");
    // 手工模版没有报价备注列，后端 manual_blogger 的 compare_field_names 不含它（传了会 422）
    expect(values).not.toContain("quote_note");
  });

  it("灰豚抖音来源 = 博主来源的全部字段 + 报价备注（与后端 compare_field_names 一致，8b）", () => {
    const douyin = conflictFieldOptions("huitun_douyin");
    const manual = conflictFieldOptions("manual_blogger");
    expect(douyin.filter((o) => o.value !== "quote_note")).toEqual(manual);
    expect(douyin.map((o) => o.value)).toContain("quote_note");
    expect(douyin).toHaveLength(15);
    expect(CONFLICT_FIELD_LABELS.huitun_douyin.quote_note).toBe("报价备注");
  });

  it("商品资料来源列出款式 / SKU / 商品的比较字段（8a-4）", () => {
    const values = conflictFieldOptions("manual_style_sku").map((o) => o.value);
    expect(values.sort()).toEqual(
      [
        "external_image_url",
        "color",
        "size",
        "base_price",
        "cost_price",
        "purchase_price",
        "tag_price",
        "sourcing_type",
        "short_name",
        "brand_id",
        "season",
      ].sort()
    );
    expect(CONFLICT_FIELD_LABELS.manual_style_sku.brand_id).toBe("品牌");
  });

  it("没选来源或未知来源时没有选项", () => {
    expect(conflictFieldOptions(undefined)).toEqual([]);
    expect(conflictFieldOptions("qianniu")).toEqual([]);
  });
});

describe("conflictValueText", () => {
  it("优先用显示文本，空值显示（空），数组用顿号连接", () => {
    expect(conflictValueText("品牌甲", "uuid")).toBe("品牌甲");
    expect(conflictValueText(null, null)).toBe("（空）");
    expect(conflictValueText(null, "")).toBe("（空）");
    expect(conflictValueText(null, ["美妆", "护肤"])).toBe("美妆、护肤");
    expect(conflictValueText(null, 1000)).toBe("1000");
  });
});
