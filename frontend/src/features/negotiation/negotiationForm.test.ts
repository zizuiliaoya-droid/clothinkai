import dayjs from "dayjs";
import { describe, expect, it } from "vitest";
import type { GoodsMember } from "@/features/promotion/types";
import {
  GOODS_REBUILT_HINT,
  membersFromItems,
  missingFieldsFor,
  negotiationFormInitial,
  negotiationItemsPayload,
  negotiationPayload,
  negotiationSubmitMissing,
  rebuildOnGoodsChange,
  SUBMIT_GATE_LABEL,
  type NegotiationFormValues,
} from "./negotiationForm";
import type { Negotiation, NegotiationItem } from "./types";

const top: GoodsMember = { style_id: "top", display_short_name: "上衣", goods_title: "条纹上衣" };
const pants: GoodsMember = { style_id: "pants", display_short_name: "阔腿裤", goods_title: "高腰阔腿裤" };
const skirt: GoodsMember = { style_id: "skirt", display_short_name: "短裙", goods_title: "飞狐短裙" };

function item(style_id: string, sku_id: string | null): NegotiationItem {
  return {
    id: `i-${style_id}`,
    style_id,
    style_code: `CODE-${style_id}`,
    display_short_name: style_id === "top" ? "上衣" : "阔腿裤",
    goods_title: style_id === "top" ? "条纹上衣" : "高腰阔腿裤",
    sku_id,
    color: sku_id ? "黑色" : null,
    size: sku_id ? "M" : null,
    style_main_image_url: null,
  };
}

const row: Pick<
  Negotiation,
  | "blogger_id"
  | "style_id"
  | "goods_main_id"
  | "cooperation_mode"
  | "platform"
  | "scheduled_publish_date"
  | "quote_amount"
  | "remark"
  | "items"
> = {
  blogger_id: "b1",
  style_id: "top",
  goods_main_id: "g1",
  cooperation_mode: "寄拍",
  platform: "抖音",
  scheduled_publish_date: "2026-10-20",
  quote_amount: "1200.00",
  remark: "带货",
  items: [item("top", "k1"), item("pants", null)],
};

const full: NegotiationFormValues = {
  blogger_id: "b1",
  style_id: "top",
  goods_main_id: "g1",
  cooperation_mode: "寄拍",
  platform: "小红书",
  scheduled_publish_date: dayjs("2026-10-20"),
  quote_amount: 800,
  remark: "  ",
  items: { top: "k1", pants: "k2" },
};

describe("membersFromItems / negotiationFormInitial", () => {
  it("成员 = 明细的款式（按明细顺序），不带编码", () => {
    expect(membersFromItems(row)).toEqual([top, pants]);
    expect(JSON.stringify(membersFromItems(row))).not.toContain("CODE-");
    expect(membersFromItems({ items: [] })).toEqual([]);
  });

  it("编辑初值：日期转 Dayjs、服务费转数字、只回填选了的 sku", () => {
    const v = negotiationFormInitial(row);
    expect(v.scheduled_publish_date?.format("YYYY-MM-DD")).toBe("2026-10-20");
    expect(v.quote_amount).toBe(1200);
    expect(v.items).toEqual({ top: "k1" });
    expect(v).toMatchObject({ blogger_id: "b1", style_id: "top", goods_main_id: "g1", platform: "抖音", remark: "带货" });
  });

  it("没有日期 / 服务费时给 null", () => {
    const v = negotiationFormInitial({ ...row, scheduled_publish_date: null, quote_amount: null, items: [] });
    expect(v.scheduled_publish_date).toBeNull();
    expect(v.quote_amount).toBeNull();
    expect(v.items).toEqual({});
  });
});

describe("negotiationItemsPayload / negotiationPayload", () => {
  it("每个成员一行、按成员顺序，没选的 sku = null", () => {
    expect(negotiationItemsPayload([top, pants], { pants: "k2" })).toEqual([
      { style_id: "top", sku_id: null },
      { style_id: "pants", sku_id: "k2" },
    ]);
    expect(negotiationItemsPayload([top], undefined)).toEqual([{ style_id: "top", sku_id: null }]);
  });

  it("组请求体：日期 YYYY-MM-DD、服务费字符串、备注去空白后空 = null、带明细", () => {
    expect(negotiationPayload(full, [top, pants])).toEqual({
      blogger_id: "b1",
      style_id: "top",
      goods_main_id: "g1",
      cooperation_mode: "寄拍",
      platform: "小红书",
      scheduled_publish_date: "2026-10-20",
      quote_amount: "800",
      remark: null,
      items: [
        { style_id: "top", sku_id: "k1" },
        { style_id: "pants", sku_id: "k2" },
      ],
    });
  });

  it("没选商品不传 goods_main_id、不知道成员不传 items（交后端推定 / 建空行）；服务费 0 照传", () => {
    const p = negotiationPayload(
      { ...full, goods_main_id: null, quote_amount: 0, scheduled_publish_date: null },
      []
    );
    expect(p).not.toHaveProperty("goods_main_id");
    expect(p).not.toHaveProperty("items");
    expect(p.quote_amount).toBe("0");
    expect(p.scheduled_publish_date).toBeNull();
  });
});

describe("rebuildOnGoodsChange", () => {
  it("款式集合没变（顺序无关）→ 保留已选", () => {
    expect(rebuildOnGoodsChange([top, pants], [pants, top], { top: "k1" })).toEqual({
      items: { top: "k1" },
      rebuilt: false,
    });
  });

  it("换了商品 → 整组清空并提示重建", () => {
    expect(rebuildOnGoodsChange([top, pants], [top, skirt], { top: "k1", pants: "k2" })).toEqual({
      items: {},
      rebuilt: true,
    });
    expect(rebuildOnGoodsChange([top, pants], [top], { top: "k1" })).toEqual({ items: {}, rebuilt: true });
    expect(GOODS_REBUILT_HINT).toBe("已按新商品重建颜色尺码，请重新选择");
  });

  it("第一次拿到成员不算重建", () => {
    expect(rebuildOnGoodsChange([], [top], undefined)).toEqual({ items: {}, rebuilt: false });
  });
});

describe("negotiationSubmitMissing", () => {
  it("填齐 → 无缺项", () => {
    expect(negotiationSubmitMissing(full, [top, pants])).toEqual([]);
  });

  it("全空 → 按后端卡点顺序报 6 项（服务费不查），文案同后端", () => {
    const missing = negotiationSubmitMissing({}, []);
    expect(missing.map((m) => m.key)).toEqual([
      "blogger_id",
      "style_id",
      "goods_items",
      "cooperation_mode",
      "platform",
      "scheduled_publish_date",
    ]);
    for (const m of missing) expect(m.label).toBe(SUBMIT_GATE_LABEL[m.key as keyof typeof SUBMIT_GATE_LABEL]);
  });

  it("套装少选一个成员也算缺颜色尺码", () => {
    expect(negotiationSubmitMissing({ ...full, items: { top: "k1" } }, [top, pants])).toEqual([
      { key: "goods_items", label: "颜色尺码" },
    ]);
  });
});

describe("missingFieldsFor", () => {
  it("字段键直接映射；颜色尺码标在没选的成员行", () => {
    const r = missingFieldsFor(
      [
        { key: "blogger_id", label: "博主" },
        { key: "goods_items", label: "颜色尺码" },
        { key: "scheduled_publish_date", label: "约定发布时间" },
      ],
      [top, pants],
      { top: "k1" }
    );
    expect(r.fields).toEqual([
      { name: "blogger_id", errors: ["必填"] },
      { name: "scheduled_publish_date", errors: ["必填"] },
      { name: ["items", "pants"], errors: ["必填"] },
    ]);
    expect(r.rest).toBeNull();
  });

  it("每行都选了还报缺（sku 已删 / 成员变了）→ 标全部行", () => {
    const r = missingFieldsFor([{ key: "goods_items", label: "颜色尺码" }], [top, pants], {
      top: "k1",
      pants: "k2",
    });
    expect(r.fields.map((f) => f.name)).toEqual([
      ["items", "top"],
      ["items", "pants"],
    ]);
  });

  it("还没有成员行 / 不认识的键 → 进顶部「缺：…」", () => {
    expect(missingFieldsFor([{ key: "goods_items", label: "颜色尺码" }], [], undefined)).toEqual({
      fields: [],
      rest: "缺：颜色尺码",
    });
    expect(
      missingFieldsFor(
        [
          { key: "unknown", label: "别的" },
          { key: "goods_items", label: "颜色尺码" },
        ],
        [],
        undefined
      ).rest
    ).toBe("缺：别的、颜色尺码");
  });
});
