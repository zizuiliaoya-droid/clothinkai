import { describe, expect, it } from "vitest";
import { toBloggerUpdate } from "./edit";
import type { BloggerCreate } from "./types";

const base: BloggerCreate = { xiaohongshu_id: "历史昵称号", nickname: "新昵称", wechat: "wx1" };

describe("toBloggerUpdate", () => {
  it("报价备注被遮挡（null）且没改：不放进提交，不会清空库里的值", () => {
    for (const v of [null, ""]) {
      const out = toBloggerUpdate({ ...base, quote_note: v }, { quote_note: null });
      expect("quote_note" in out).toBe(false);
      expect(out).toEqual(base);
    }
  });

  it("没渲染报价备注表单项（undefined）：不放进提交", () => {
    const out = toBloggerUpdate({ ...base, quote_note: undefined }, { quote_note: "图文500" });
    expect("quote_note" in out).toBe(false);
  });

  it("可见且没改（含首尾空白差别）：不放进提交", () => {
    const out = toBloggerUpdate({ ...base, quote_note: " 图文500 " }, { quote_note: "图文500" });
    expect("quote_note" in out).toBe(false);
  });

  it("改了：放进提交（含主动清空）", () => {
    expect(
      toBloggerUpdate({ ...base, quote_note: "图文600" }, { quote_note: "图文500" }).quote_note
    ).toBe("图文600");
    const cleared = toBloggerUpdate({ ...base, quote_note: "" }, { quote_note: "图文500" });
    expect("quote_note" in cleared).toBe(true);
    expect(cleared.quote_note).toBe("");
    expect(
      toBloggerUpdate({ ...base, quote_note: "图文1" }, { quote_note: null }).quote_note
    ).toBe("图文1");
  });

  it("其余字段原样带上", () => {
    expect(toBloggerUpdate({ ...base, quote_note: null }, { quote_note: null })).toEqual(base);
  });
});
