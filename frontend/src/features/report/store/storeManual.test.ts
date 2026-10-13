// 店铺日手填（7e R4 S8）：金额校验与「只发改了的字段」。
import { describe, expect, it } from "vitest";
import {
  MANUAL_AMOUNT_ERROR,
  manualDraftFromRow,
  manualErrors,
  manualPatch,
  validateManualAmount,
  type StoreManualDraft,
} from "./storeManual";

const ORIGINAL: StoreManualDraft = {
  ad_spend_total: null,
  zhitongche_spend: "40.00",
  yinli_spend: null,
  refund_only_amount: "10.00",
};

describe("validateManualAmount", () => {
  it.each([null, undefined, "", "  "])("空（%s）= 清空，不报错", (v) => {
    expect(validateManualAmount(v)).toBeNull();
  });

  it.each(["0", "0.5", "12.30", "99999999.99"])("%s 通过", (v) => {
    expect(validateManualAmount(v)).toBeNull();
  });

  it.each(["-1", "1.234", "1e8", "abc", "100000000", "99999999.991", "1,000", "+1"])(
    "%s 报错",
    (v) => {
      expect(validateManualAmount(v)).toBe(MANUAL_AMOUNT_ERROR);
    }
  );

  it("提示文案同后端", () => {
    expect(MANUAL_AMOUNT_ERROR).toBe("金额需在 0 ~ 99,999,999.99 之间，最多两位小数");
  });
});

describe("manualErrors", () => {
  it("只列出有错的字段", () => {
    expect(manualErrors({ ...ORIGINAL, yinli_spend: "-1", refund_only_amount: "" })).toEqual({
      yinli_spend: MANUAL_AMOUNT_ERROR,
    });
  });
});

describe("manualPatch", () => {
  it("没改动 → 空对象；数值相同写法不同（40 与 40.00）不算改动", () => {
    expect(manualPatch(ORIGINAL, { ...ORIGINAL })).toEqual({});
    expect(manualPatch(ORIGINAL, { ...ORIGINAL, zhitongche_spend: "40" })).toEqual({});
    expect(manualPatch(ORIGINAL, { ...ORIGINAL, ad_spend_total: "" })).toEqual({});
  });

  it("只含改了的字段；清空发 null；新填的发去掉空白的原文", () => {
    expect(
      manualPatch(ORIGINAL, {
        ...ORIGINAL,
        zhitongche_spend: "45.5",
        refund_only_amount: null,
        yinli_spend: " 0 ",
      })
    ).toEqual({ zhitongche_spend: "45.5", refund_only_amount: null, yinli_spend: "0" });
  });

  it("清空用空串表示也发 null", () => {
    expect(manualPatch(ORIGINAL, { ...ORIGINAL, zhitongche_spend: "" })).toEqual({
      zhitongche_spend: null,
    });
  });
});

describe("manualDraftFromRow", () => {
  it("只取四个手填字段", () => {
    expect(
      manualDraftFromRow({
        ad_spend_total: null,
        zhitongche_spend: "40.00",
        yinli_spend: null,
        refund_only_amount: "10.00",
      })
    ).toEqual(ORIGINAL);
  });
});
