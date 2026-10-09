import { describe, expect, it } from "vitest";
import { extractGateMissing, missingToFields } from "./missingToFields";

const MISSING = [
  { key: "receiver_phone", label: "收件电话" },
  { key: "sku", label: "颜色尺码" },
  { key: "payment_qr", label: "收款码" },
];

describe("missingToFields", () => {
  it("能映射的变成表单字段并标「必填」，映射不到的拼成一行「缺：…」", () => {
    const out = missingToFields(MISSING, {
      receiver_phone: "receiver_phone",
      sku: ["items", 0, "sku_id"],
    });
    expect(out.fields).toEqual([
      { name: "receiver_phone", errors: ["必填"] },
      { name: ["items", 0, "sku_id"], errors: ["必填"] },
    ]);
    expect(out.rest).toBe("缺：收款码");
  });

  it("全都映射到时 rest 为 null；全都映射不到时 fields 为空", () => {
    expect(missingToFields(MISSING.slice(0, 1), { receiver_phone: "phone" })).toEqual({
      fields: [{ name: "phone", errors: ["必填"] }],
      rest: null,
    });
    expect(missingToFields(MISSING, {})).toEqual({
      fields: [],
      rest: "缺：收件电话、颜色尺码、收款码",
    });
  });

  it("空列表 → 什么都没有", () => {
    expect(missingToFields([], { a: "a" })).toEqual({ fields: [], rest: null });
  });
});

describe("extractGateMissing", () => {
  function apiErr(code: string, details?: unknown) {
    return { response: { status: 422, data: { code, message: "缺：收件电话", details } } };
  }

  it("FLOW_GATE_MISSING → 取出 missing", () => {
    expect(extractGateMissing(apiErr("FLOW_GATE_MISSING", { missing: MISSING }))).toEqual(MISSING);
  });

  it("别的错误码、不是接口错误、形状不对 → null", () => {
    expect(extractGateMissing(apiErr("ILLEGAL_STATE_TRANSITION", { missing: MISSING }))).toBeNull();
    expect(extractGateMissing(new Error("network"))).toBeNull();
    expect(extractGateMissing(null)).toBeNull();
    expect(extractGateMissing(apiErr("FLOW_GATE_MISSING"))).toBeNull();
    expect(extractGateMissing(apiErr("FLOW_GATE_MISSING", { missing: [{ key: 1 }] }))).toBeNull();
  });
});
