import { describe, expect, it } from "vitest";
import { mergeOptions, type PickerOption } from "./mergeSelectedOption";

const A: PickerOption = { value: "id-a", label: "博主甲 (xhs_a)" };
const B: PickerOption = { value: "id-b", label: "博主乙 (xhs_b)" };
const C: PickerOption = { value: "id-c", label: "博主丙 (xhs_c)" };

describe("mergeOptions", () => {
  it("结果里已有已选值：原样返回，不重复插入", () => {
    const merged = mergeOptions([A, B], "id-b", { ...B, label: "刚选的乙" }, B);
    expect(merged).toEqual([A, B]);
  });

  it("已选值不在结果里：pinned 优先放最前", () => {
    const pinned = { value: "id-c", label: "刚选的丙" };
    const selected = { value: "id-c", label: "单据上的丙" };
    expect(mergeOptions([A, B], "id-c", pinned, selected)).toEqual([pinned, A, B]);
  });

  it("没有 pinned 时用父组件给的回显（编辑谈款显示名字不是 UUID）", () => {
    expect(mergeOptions([A, B], "id-c", null, C)).toEqual([C, A, B]);
    expect(mergeOptions([], "id-c", undefined, C)).toEqual([C]);
  });

  it("pinned 的 value 和当前 value 不同：不插 pinned，回落到 selected", () => {
    const stalePinned = { value: "id-a", label: "上一张单据选的甲" };
    expect(mergeOptions([B], "id-c", stalePinned, C)).toEqual([C, B]);
  });

  it("pinned / selected 都和当前 value 对不上：一个都不插", () => {
    expect(mergeOptions([B], "id-c", A, A)).toEqual([B]);
  });

  it("value 为空（未选 / 被清空 / 表单重置）不插", () => {
    for (const empty of ["", null, undefined]) {
      expect(mergeOptions([A], empty, C, C)).toEqual([A]);
    }
  });

  it("结果按 value 去重，先到先得", () => {
    const dup = { value: "id-a", label: "重复的甲" };
    expect(mergeOptions([A, dup, B], "id-a")).toEqual([A, B]);
  });

  it("不改入参", () => {
    const results = [A, B];
    mergeOptions(results, "id-c", C, C);
    expect(results).toEqual([A, B]);
  });
});
