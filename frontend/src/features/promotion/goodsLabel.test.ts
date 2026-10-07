import { describe, expect, it } from "vitest";
import type { GoodsOption } from "@/features/product/api";
import { goodsNameLabel } from "./goodsLabel";

const CODE = "SUIT-1074568657697";

function option(overrides: Partial<GoodsOption> = {}): GoodsOption {
  return {
    goods_main_id: "g-1",
    goods_code: CODE,
    goods_title: "2026秋冬法式复古高腰A字半身裙",
    goods_short_name: "飞狐短裙",
    is_suit: false,
    ...overrides,
  };
}

describe("goodsNameLabel", () => {
  it("有简称 → 简称", () => {
    expect(goodsNameLabel(option())).toBe("飞狐短裙");
  });

  it("没简称 → 全称", () => {
    expect(goodsNameLabel(option({ goods_short_name: null }))).toBe(
      "2026秋冬法式复古高腰A字半身裙"
    );
    expect(goodsNameLabel(option({ goods_short_name: "" }))).toBe(
      "2026秋冬法式复古高腰A字半身裙"
    );
  });

  it("套装带「（套装）」", () => {
    expect(goodsNameLabel(option({ is_suit: true }))).toBe("飞狐短裙（套装）");
  });

  it("不含商品编码", () => {
    for (const g of [
      option(),
      option({ is_suit: true }),
      option({ goods_short_name: null, is_suit: true }),
    ]) {
      expect(goodsNameLabel(g)).not.toContain(CODE);
    }
  });
});
