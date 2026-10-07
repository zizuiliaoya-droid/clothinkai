import { describe, expect, it } from "vitest";
import type { GoodsOption } from "./api";
import { goodsPickerLabel, platformIdsByGoods } from "./goodsLabels";

function opt(id: string, code: string, title: string, shortName: string | null, isSuit = false): GoodsOption {
  return {
    goods_main_id: id,
    goods_code: code,
    goods_title: title,
    goods_short_name: shortName,
    is_suit: isSuit,
  };
}

describe("goodsPickerLabel", () => {
  const single = opt("g1", "260415", "冰雪飞狐皮草外套长款", "冰雪飞狐");
  const dup = opt("g2", "260415-1074568657697", "冰雪飞狐另一个全称", "冰雪飞狐");
  const suit = opt("g3", "SUIT-260415_260419", "春日套装两件套", "春日套装", true);

  it("不同名时只显示显示名与套装标记", () => {
    const options = [single, suit];
    expect(goodsPickerLabel(single, {}, options)).toBe("冰雪飞狐");
    expect(goodsPickerLabel(suit, {}, options)).toBe("春日套装（套装）");
  });

  it("没填简称回落全称", () => {
    const noShort = opt("g4", "260499", "没填简称的全称", null);
    expect(goodsPickerLabel(noShort, {}, [noShort])).toBe("没填简称的全称");
  });

  it("同名时追加本款的平台 ID", () => {
    const options = [single, dup, suit];
    const links = { g1: ["665201", "665202", "665203"], g2: ["665300"] };
    expect(goodsPickerLabel(single, links, options)).toBe("冰雪飞狐 · 平台ID 665201、665202 等");
    expect(goodsPickerLabel(dup, links, options)).toBe("冰雪飞狐 · 平台ID 665300");
    expect(goodsPickerLabel(suit, links, options)).toBe("春日套装（套装）");
  });

  it("同名但没有链接写「本款无链接」", () => {
    expect(goodsPickerLabel(dup, { g1: ["665201"] }, [single, dup])).toBe("冰雪飞狐 · 本款无链接");
  });

  it("结果里从不含 goods_code", () => {
    const options = [single, dup, suit];
    const links = { g1: ["665201"], g2: [], g3: ["665400"] };
    for (const o of options) {
      expect(goodsPickerLabel(o, links, options)).not.toContain(o.goods_code);
    }
  });
});

describe("platformIdsByGoods", () => {
  it("按归属商品分组，没有归属的跳过", () => {
    expect(
      platformIdsByGoods([
        { goods_main_id: "g1", platform_id: "1" },
        { goods_main_id: null, platform_id: "2" },
        { goods_main_id: "g1", platform_id: "3" },
        { goods_main_id: "g2", platform_id: "4" },
      ])
    ).toEqual({ g1: ["1", "3"], g2: ["4"] });
  });
});
