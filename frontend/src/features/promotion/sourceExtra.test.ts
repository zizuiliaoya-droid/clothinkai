import { describe, expect, it } from "vitest";
import { buildSourceExtraPatch } from "./sourceExtra";

const FIELDS = ["颜色及规格", "打单地址", "发货单号", "订单号", "合作形式", "负责PR"] as const;

describe("buildSourceExtraPatch", () => {
  it("只含改过的键", () => {
    const initial = { 打单地址: "杭州", 订单号: "", 负责PR: "小王" };
    const values = { 打单地址: "杭州", 订单号: "TB001", 负责PR: "小王" };
    expect(buildSourceExtraPatch(initial, values, FIELDS)).toEqual({ 订单号: "TB001" });
  });

  it("没碰过的空字段不提交（含表单给的 undefined）", () => {
    const initial = { 颜色及规格: "", 发货单号: "" };
    const values = { 颜色及规格: undefined, 发货单号: "" };
    expect(buildSourceExtraPatch(initial, values, FIELDS)).toEqual({});
  });

  it("清空 → null（后端删这个键）", () => {
    const initial = { 订单号: "TB001", 合作形式: "线下" };
    const values = { 订单号: "", 合作形式: undefined };
    expect(buildSourceExtraPatch(initial, values, FIELDS)).toEqual({
      订单号: null,
      合作形式: null,
    });
  });

  it("全空白等同空；只差首尾空白不算改动；新值去空白", () => {
    expect(buildSourceExtraPatch({ 订单号: "TB001" }, { 订单号: "   " }, FIELDS)).toEqual({
      订单号: null,
    });
    expect(buildSourceExtraPatch({ 订单号: "" }, { 订单号: "  " }, FIELDS)).toEqual({});
    expect(buildSourceExtraPatch({ 订单号: "TB001" }, { 订单号: " TB001 " }, FIELDS)).toEqual(
      {}
    );
    expect(buildSourceExtraPatch({ 订单号: "" }, { 订单号: " TB002 " }, FIELDS)).toEqual({
      订单号: "TB002",
    });
  });

  it("非字符串的旧值按字符串比较", () => {
    expect(buildSourceExtraPatch({ 订单号: 123 }, { 订单号: "123" }, FIELDS)).toEqual({});
  });

  it("fieldNames 之外的键不出现", () => {
    const initial = { 订单号: "", 博主风格: "甜美", 寄回单号: "SF0001" };
    const values = { 订单号: "TB001", 博主风格: "改了也不交", 寄回单号: "" };
    const patch = buildSourceExtraPatch(initial, values, FIELDS);
    expect(patch).toEqual({ 订单号: "TB001" });
    expect(Object.keys(patch)).not.toContain("博主风格");
    expect(Object.keys(patch)).not.toContain("寄回单号");
  });
});
