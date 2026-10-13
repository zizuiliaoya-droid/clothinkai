// BI 单品下拉的选项文字（7e R6 评审 r1 L1）：与后端 bi_chart_labels 同一套区分规则。
import { describe, expect, it } from "vitest";
import type { BiGoodsRow } from "../types";
import { goodsOptionLabels } from "./biFormat";

function row(over: Partial<BiGoodsRow>): BiGoodsRow {
  return {
    goods_id: "g",
    goods_title: "条纹针织上衣",
    goods_short_name: "条纹上衣",
    is_suit: false,
    style_codes: [],
    main_image_url: null,
    pay_amount: "0.00",
    refund_amount: "0.00",
    return_rate: null,
    confirmed_amount: "0.00",
    internal_spend: "0.00",
    external_spend: "0.00",
    total_spend: "0.00",
    net_roi: null,
    ...over,
  };
}

describe("goodsOptionLabels", () => {
  it("显示名不重复：只给套装加后缀", () => {
    expect(
      goodsOptionLabels([row({ style_codes: ["K1"] }), row({ is_suit: true, style_codes: ["K2"] })])
    ).toEqual(["条纹上衣", "条纹上衣（套装）"]);
  });

  it("显示名重复：追加款号区分，超过 3 个写前 3 个加「等」；没有款号的照旧", () => {
    expect(
      goodsOptionLabels([
        row({ style_codes: ["K1"] }),
        row({ style_codes: ["K2", "K3", "K4", "K5"] }),
        row({ style_codes: [] }),
        row({ goods_short_name: "别的", style_codes: ["K9"] }),
      ])
    ).toEqual(["条纹上衣（款号 K1）", "条纹上衣（款号 K2、K3、K4等）", "条纹上衣", "别的"]);
  });
});
