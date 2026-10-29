import { describe, expect, it } from "vitest";
import { formatMoney } from "./money";
import { money } from "@/features/report/bi/biFormat";

describe("人民币符号与负号", () => {
  it.each([
    ["-200.00", "-¥200.00"], ["-1,234.560", "-¥1,234.560"],
    ["200.0", "¥200.0"], ["0.00", "¥0.00"], [null, "—"], [undefined, "—"],
  ])("%s保持调用处原精度", (value, expected) => expect(formatMoney(value)).toBe(expected));
  it("BI保留原千分位与两位小数", () => {
    expect(money("-1234.5")).toBe("-¥1,234.50");
    expect(money("1234.5")).toBe("¥1,234.50");
    expect(money(null)).toBe("—");
  });
});
