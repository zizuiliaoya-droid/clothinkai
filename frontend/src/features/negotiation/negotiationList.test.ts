import { describe, expect, it } from "vitest";
import type { NegotiationItem } from "./types";
import {
  ITEM_NOT_SELECTED,
  NEGOTIATION_TABS,
  negotiationImages,
  negotiationItemLines,
} from "./negotiationList";

const SKU_CODE = "SKU-7788-BLK-M";

function item(over: Partial<NegotiationItem> = {}): NegotiationItem {
  return {
    id: "i-1",
    style_id: "s-1",
    style_code: "ST-001",
    display_short_name: "飞狐短裙",
    goods_title: "2026秋冬法式复古高腰A字半身裙",
    sku_id: "k-1",
    color: "黑色",
    size: "M",
    style_main_image_url: "https://img.example.com/s1.jpg",
    ...over,
  };
}

describe("NEGOTIATION_TABS", () => {
  it("全部 + 7 态，顺序同设计 8.3，审核驳回显示「审批未通过」", () => {
    expect(NEGOTIATION_TABS.map((t) => t.label)).toEqual([
      "全部",
      "主管待审",
      "待老板审核",
      "老板退回",
      "待补收货地址",
      "草稿",
      "审批未通过",
      "已定稿",
    ]);
    expect(NEGOTIATION_TABS[0].key).toBe("");
    expect(NEGOTIATION_TABS.find((t) => t.label === "审批未通过")?.key).toBe("审核驳回");
  });
});

describe("negotiationItemLines", () => {
  it("单品只写颜色尺码", () => {
    expect(negotiationItemLines({ goods_is_suit: false, items: [item()] })).toEqual(["黑色 / M"]);
  });

  it("套装每个成员一行「简称 · 颜色 / 尺码」，没选的写「颜色尺码未选」", () => {
    expect(
      negotiationItemLines({
        goods_is_suit: true,
        items: [
          item(),
          item({ id: "i-2", style_id: "s-2", display_short_name: "飞狐上衣", sku_id: null, color: null, size: null }),
        ],
      })
    ).toEqual(["飞狐短裙 · 黑色 / M", `飞狐上衣 · ${ITEM_NOT_SELECTED}`]);
  });

  it("单品没选 → 「颜色尺码未选」；存量没有明细 → 空", () => {
    expect(
      negotiationItemLines({ goods_is_suit: false, items: [item({ sku_id: null, color: null, size: null })] })
    ).toEqual([ITEM_NOT_SELECTED]);
    expect(negotiationItemLines({ goods_is_suit: false, items: [] })).toEqual([]);
  });

  it("不含 SKU 编码与款式编码", () => {
    const lines = negotiationItemLines({ goods_is_suit: true, items: [item(), item({ id: "i-2", style_id: "s-2" })] });
    for (const line of lines) {
      expect(line).not.toContain(SKU_CODE);
      expect(line).not.toContain("ST-001");
    }
  });
});

describe("negotiationImages", () => {
  it("每个成员款式一张，没图的不占位，同款去重", () => {
    const images = negotiationImages({
      items: [
        item(),
        item({ id: "i-2", style_id: "s-2", style_main_image_url: null }),
        item({ id: "i-3", style_id: "s-3", style_main_image_url: "https://img.example.com/s3.jpg" }),
        item({ id: "i-4" }),
      ],
    });
    expect(images.map((i) => [i.style_id, i.url])).toEqual([
      ["s-1", "https://img.example.com/s1.jpg"],
      ["s-3", "https://img.example.com/s3.jpg"],
    ]);
  });

  it("都没有图 → 空（GoodsImages 显示「暂无主图」）", () => {
    expect(negotiationImages({ items: [item({ style_main_image_url: null })] })).toEqual([]);
    expect(negotiationImages({ items: [] })).toEqual([]);
  });
});
