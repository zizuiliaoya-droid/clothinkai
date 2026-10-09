import { describe, expect, it } from "vitest";
import {
  bloggerOptionLabel,
  bloggerPickerParams,
  bloggerPickerQueryKey,
} from "./picker";

describe("bloggerPickerQueryKey", () => {
  it("缓存键按平台分开，没传平台用 *（换平台不串缓存）", () => {
    expect(bloggerPickerQueryKey("抖音")).toEqual(["bloggers", "picker-options", "抖音"]);
    expect(bloggerPickerQueryKey("小红书")).toEqual(["bloggers", "picker-options", "小红书"]);
    expect(bloggerPickerQueryKey(undefined)).toEqual(["bloggers", "picker-options", "*"]);
    expect(bloggerPickerQueryKey("")).toEqual(["bloggers", "picker-options", "*"]);
    expect(bloggerPickerQueryKey("抖音")).not.toEqual(bloggerPickerQueryKey("小红书"));
  });

  it("前缀仍在 [bloggers] 下（博主增删改后 invalidate [bloggers] 能刷到下拉）", () => {
    expect(bloggerPickerQueryKey("抖音").slice(0, 1)).toEqual(["bloggers"]);
  });
});

describe("bloggerPickerParams", () => {
  it("带上平台与关键词", () => {
    expect(bloggerPickerParams("abc", "抖音", 20)).toEqual({
      page: 1,
      page_size: 20,
      keyword: "abc",
      platform: "抖音",
    });
  });

  it("没传平台时不带 platform（推广单页行为同现在）", () => {
    const params = bloggerPickerParams(undefined, undefined, 20);
    expect(params).toEqual({ page: 1, page_size: 20, keyword: undefined });
    expect("platform" in params).toBe(false);
    expect("platform" in bloggerPickerParams("x", "", 20)).toBe(false);
  });
});

describe("bloggerOptionLabel", () => {
  it("昵称（平台·账号）", () => {
    expect(
      bloggerOptionLabel({ nickname: "测试博主甲", platform: "抖音", xiaohongshu_id: "T00X01" })
    ).toBe("测试博主甲（抖音·T00X01）");
    expect(
      bloggerOptionLabel({ nickname: "测试博主乙", platform: "小红书", xiaohongshu_id: "fake_001" })
    ).toBe("测试博主乙（小红书·fake_001）");
  });
});
