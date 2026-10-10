import dayjs from "dayjs";
import { describe, expect, it } from "vitest";
import { buildCreatePayload } from "./createPayload";
import { SOURCE_FIELD_NAMES, SOURCE_LIST_COLUMNS } from "./listConstants";

const base = {
  style_id: "s1",
  blogger_id: "b1",
  cooperation_mode: "寄拍",
  platform: "小红书",
  cooperation_date: dayjs("2026-10-10"),
};

describe("buildCreatePayload", () => {
  it("没勾「需要仓库发货」→ need_shipping=false（11-58：补录历史单不进发货）", () => {
    expect(buildCreatePayload(base).need_shipping).toBe(false);
    expect(buildCreatePayload({ ...base, need_shipping: false }).need_shipping).toBe(false);
  });

  it("勾了 → need_shipping=true", () => {
    expect(buildCreatePayload({ ...base, need_shipping: true }).need_shipping).toBe(true);
  });

  it("其余字段照旧", () => {
    expect(buildCreatePayload({ ...base, quote_amount: 300, note_title: "", remark: "备" })).toEqual({
      style_id: "s1",
      goods_main_id: null,
      blogger_id: "b1",
      cooperation_mode: "寄拍",
      platform: "小红书",
      cooperation_date: "2026-10-10",
      quote_amount: "300",
      note_title: null,
      remark: "备",
      need_shipping: false,
    });
  });
});

describe("录入信息字段", () => {
  it("表单只剩订单号 / 合作形式 / 负责PR（颜色及规格、打单地址、发货单号随 PR-2 删）", () => {
    expect(SOURCE_FIELD_NAMES).toEqual(["订单号", "合作形式", "负责PR"]);
  });

  it("列表只读列保留颜色及规格旧值，不再有打单地址 / 发货单号", () => {
    expect(SOURCE_LIST_COLUMNS).toEqual(["颜色及规格", "订单号", "合作形式", "负责PR"]);
  });
});
