import { describe, expect, it } from "vitest";
import type { PromotionItem } from "./types";
import { colorSizeLabel, itemSpecLines, shipDetailLines } from "./shipDisplay";

const SKU_CODE = "SKU-7788-BLK-M";

function item(overrides: Partial<PromotionItem> = {}): PromotionItem {
  return {
    style_id: "s-1",
    display_short_name: "飞狐短裙",
    goods_title: "2026秋冬法式复古高腰A字半身裙",
    sku_id: "k-1",
    color: "黑色",
    size: "M",
    style_main_image_url: null,
    ...overrides,
  };
}

describe("colorSizeLabel", () => {
  it("颜色 / 尺码，去两端空白", () => {
    expect(colorSizeLabel("黑色", "M")).toBe("黑色 / M");
    expect(colorSizeLabel(" 黑色 ", " M ")).toBe("黑色 / M");
  });

  it("缺一项只显示另一项，都没有回「—」", () => {
    expect(colorSizeLabel("黑色", "")).toBe("黑色");
    expect(colorSizeLabel("  ", "M")).toBe("M");
    expect(colorSizeLabel("", "  ")).toBe("—");
  });
});

describe("itemSpecLines", () => {
  it("单品一行，只写颜色尺码", () => {
    expect(itemSpecLines({ items: [item()], legacy_color_spec: null })).toEqual({
      lines: ["黑色 / M"],
      legacy: false,
    });
  });

  it("套装每个成员一行「简称 · 颜色 / 尺码」，按接口顺序", () => {
    const spec = itemSpecLines({
      items: [
        item(),
        item({ style_id: "s-2", display_short_name: "飞狐上衣", color: "白色", size: "L" }),
      ],
      legacy_color_spec: null,
    });
    expect(spec).toEqual({ lines: ["飞狐短裙 · 黑色 / M", "飞狐上衣 · 白色 / L"], legacy: false });
  });

  it("有明细就不看 legacy_color_spec", () => {
    expect(itemSpecLines({ items: [item()], legacy_color_spec: "红色 S" })?.legacy).toBe(false);
  });

  it("没有明细的旧单：原文 + legacy 标记", () => {
    expect(itemSpecLines({ items: [], legacy_color_spec: " 黑色 M " })).toEqual({
      lines: ["黑色 M"],
      legacy: true,
    });
  });

  it("都没有 → null（不渲染小字）", () => {
    expect(itemSpecLines({ items: [], legacy_color_spec: null })).toBeNull();
    expect(itemSpecLines({ items: [], legacy_color_spec: "   " })).toBeNull();
    expect(itemSpecLines({ items: undefined, legacy_color_spec: undefined })).toBeNull();
  });

  it("不含 SKU 编码", () => {
    const spec = itemSpecLines({ items: [item(), item({ style_id: "s-2" })], legacy_color_spec: null });
    for (const line of spec?.lines ?? []) expect(line).not.toContain(SKU_CODE);
  });
});

describe("shipDetailLines", () => {
  const base = {
    ship_status: null,
    ship_pushed_at: null,
    ship_pushed_by_name: null,
    ship_courier: null,
    ship_waybill: null,
    shipped_at: null,
  };

  it("待打单：推送人与推送时间", () => {
    expect(
      shipDetailLines({
        ...base,
        ship_status: "待打单",
        ship_pushed_by_name: "王主管",
        ship_pushed_at: "2026-10-09T08:30:00",
      })
    ).toEqual(["推送人：王主管", "推送时间：2026-10-09 08:30"]);
  });

  it("已发货：快递公司、单号、发货时间", () => {
    expect(
      shipDetailLines({
        ...base,
        ship_status: "已发货",
        ship_courier: "顺丰",
        ship_waybill: "SF123456",
        shipped_at: "2026-10-10T14:05:00",
      })
    ).toEqual(["快递公司：顺丰", "单号：SF123456", "发货时间：2026-10-10 14:05"]);
  });

  it("缺的值写「—」", () => {
    expect(shipDetailLines({ ...base, ship_status: "待打单" })).toEqual([
      "推送人：—",
      "推送时间：—",
    ]);
  });

  it("待发货与历史单（null）没有悬停内容", () => {
    expect(shipDetailLines({ ...base, ship_status: "待发货" })).toEqual([]);
    expect(shipDetailLines(base)).toEqual([]);
  });
});
