// 旧单「录入信息」里的颜色及规格原文 → 该款的 SKU（流程线 8.4 ShipPushModal、9.5）。
import { describe, expect, it } from "vitest";
import { matchLegacyColorSpec, type LegacySkuOption } from "./matchLegacyColorSpec";

const skus: LegacySkuOption[] = [
  { id: "k1", color: "黑色", size: "M" },
  { id: "k2", color: "黑色", size: "L" },
  { id: "k3", color: "白色", size: "M" },
  { id: "k4", color: "黑色", size: "" },
];

describe("matchLegacyColorSpec", () => {
  it("「黑色 M」对上 黑色 / M", () => {
    expect(matchLegacyColorSpec("黑色 M", skus)).toBe("k1");
  });

  it("只有颜色的「黑色」对上没有尺码的那个 SKU", () => {
    expect(matchLegacyColorSpec("黑色", skus)).toBe("k4");
  });

  it("前后与中间多余空格不影响：「 黑色  M 」", () => {
    expect(matchLegacyColorSpec(" 黑色  M ", skus)).toBe("k1");
  });

  it("全角空格同样当空格", () => {
    expect(matchLegacyColorSpec("白色\u3000M", skus)).toBe("k3");
  });

  it("对不上返回 null", () => {
    expect(matchLegacyColorSpec("红色 M", skus)).toBeNull();
    expect(matchLegacyColorSpec("黑色 XL", skus)).toBeNull();
  });

  it("空串 / null / 只有空白返回 null", () => {
    expect(matchLegacyColorSpec("", skus)).toBeNull();
    expect(matchLegacyColorSpec(null, skus)).toBeNull();
    expect(matchLegacyColorSpec("   ", skus)).toBeNull();
  });

  it("同色同码有多个 SKU 时不预选", () => {
    const dup = [...skus, { id: "k5", color: "黑色", size: "M" }];
    expect(matchLegacyColorSpec("黑色 M", dup)).toBeNull();
  });

  it("SKU 自己的颜色尺码带空白也照样比", () => {
    expect(matchLegacyColorSpec("灰色 S", [{ id: "g", color: " 灰色 ", size: "S " }])).toBe("g");
  });
});
