import { describe, expect, it } from "vitest";
import { buildCreatePayload } from "./createPayload";
import { PROMOTION_IMPORT_COLUMNS, SOURCE_FIELDS } from "./listConstants";

describe("内容分类入口", () => {
  it("新建空白不覆盖博主快照，显式分类发送给服务端", () => {
    expect(buildCreatePayload({ content_category: " " })).not.toHaveProperty("content_category");
    expect(buildCreatePayload({ content_category: " 单篇 " }).content_category).toBe("单篇");
  });
  it("模板保留合作形式，录入信息不再写旧JSON键", () => {
    expect(PROMOTION_IMPORT_COLUMNS).toContain("合作形式");
    expect(PROMOTION_IMPORT_COLUMNS).toContain("小红书ID");
    expect(SOURCE_FIELDS.some((f) => f.name === "合作形式")).toBe(false);
  });
});
