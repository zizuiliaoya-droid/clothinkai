import { describe, expect, it } from "vitest";
import type { GoodsMember, Promotion, PromotionItem } from "@/features/promotion/types";
import {
  itemsChanged,
  itemsInitial,
  itemsPayload,
  legacyPreselectStyle,
  membersOf,
} from "./goodsItemsForm";

const top: GoodsMember = { style_id: "top", display_short_name: "上衣", goods_title: "条纹上衣" };
const pants: GoodsMember = { style_id: "pants", display_short_name: "阔腿裤", goods_title: "阔腿裤" };

function item(style_id: string, sku_id: string): PromotionItem {
  return {
    style_id,
    sku_id,
    display_short_name: style_id,
    goods_title: style_id,
    color: "黑色",
    size: "M",
    style_main_image_url: null,
  };
}

describe("membersOf", () => {
  it("用接口的 goods_members", () => {
    const row = { goods_members: [top, pants], style_id: "top", display_short_name: "x", style_short_name_snapshot: "y" };
    expect(membersOf(row)).toEqual([top, pants]);
  });

  it("没给时按单品回落推广单款式", () => {
    const row = { goods_members: [], style_id: "s1", display_short_name: null, style_short_name_snapshot: "快照" };
    expect(membersOf(row as unknown as Promotion)).toEqual([
      { style_id: "s1", display_short_name: "快照", goods_title: "快照" },
    ]);
  });
});

describe("itemsInitial / itemsPayload / itemsChanged", () => {
  it("初值只取成员的行", () => {
    expect(itemsInitial({ items: [item("top", "k1"), item("gone", "k9")] }, [top, pants])).toEqual({ top: "k1" });
  });

  it("按成员顺序组请求体，缺一个返回 null", () => {
    expect(itemsPayload([top, pants], { pants: "k2", top: "k1" })).toEqual([
      { style_id: "top", sku_id: "k1" },
      { style_id: "pants", sku_id: "k2" },
    ]);
    expect(itemsPayload([top, pants], { top: "k1" })).toBeNull();
    expect(itemsPayload([top], undefined)).toBeNull();
  });

  it("变没变：顺序无关、行数不同算变", () => {
    const current = [item("top", "k1"), item("pants", "k2")];
    expect(itemsChanged(current, [{ style_id: "pants", sku_id: "k2" }, { style_id: "top", sku_id: "k1" }])).toBe(false);
    expect(itemsChanged(current, [{ style_id: "top", sku_id: "k1" }, { style_id: "pants", sku_id: "k3" }])).toBe(true);
    expect(itemsChanged([], [{ style_id: "top", sku_id: "k1" }])).toBe(true);
  });
});

describe("legacyPreselectStyle", () => {
  it("没有明细、有原文、推广单款式是成员 → 预选它", () => {
    expect(legacyPreselectStyle({ items: [], legacy_color_spec: "黑色 M", style_id: "top" }, [top, pants])).toBe("top");
  });

  it("有明细 / 没原文 / 不是成员 → null", () => {
    expect(legacyPreselectStyle({ items: [item("top", "k1")], legacy_color_spec: "黑色 M", style_id: "top" }, [top])).toBeNull();
    expect(legacyPreselectStyle({ items: [], legacy_color_spec: "  ", style_id: "top" }, [top])).toBeNull();
    expect(legacyPreselectStyle({ items: [], legacy_color_spec: "黑色 M", style_id: "other" }, [top])).toBeNull();
  });
});
