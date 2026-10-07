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
      ].sort()
    );
    expect(CONFLICT_FIELD_LABELS.manual_blogger.quote).toBe("报价");
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
