import { describe, expect, it } from "vitest";
import { IMAGE_NOTE_STATUS, imageSummaryText } from "./imageSummary";

describe("imageSummaryText", () => {
  it("没读过内嵌图（null）时不显示", () => {
    expect(imageSummaryText(null)).toBeNull();
    expect(imageSummaryText(undefined)).toBeNull();
  });

  it("补了 / 已有主图跳过总显示，其余为 0 省略", () => {
    expect(imageSummaryText({ set: 0, kept: 0, invalid: 0, skipped: 0, failed: 0 })).toBe(
      "内嵌主图：补了 0 款 · 已有主图跳过 0 款"
    );
    expect(imageSummaryText({ set: 3, kept: 2, invalid: 0, skipped: 0, failed: 0 })).toBe(
      "内嵌主图：补了 3 款 · 已有主图跳过 2 款"
    );
  });

  it("非零的图片无效 / 未找到款式 / 保存失败按顺序追加", () => {
    expect(imageSummaryText({ set: 1, kept: 0, invalid: 2, skipped: 0, failed: 0 })).toBe(
      "内嵌主图：补了 1 款 · 已有主图跳过 0 款 · 图片无效 2 款"
    );
    expect(imageSummaryText({ set: 1, kept: 4, invalid: 2, skipped: 5, failed: 6 })).toBe(
      "内嵌主图：补了 1 款 · 已有主图跳过 4 款 · 图片无效 2 款 · 未找到款式 5 款 · 保存失败 6 款"
    );
    expect(imageSummaryText({ set: 0, kept: 0, invalid: 0, skipped: 0, failed: 1 })).toBe(
      "内嵌主图：补了 0 款 · 已有主图跳过 0 款 · 保存失败 1 款"
    );
  });

  it("每种行状态都有文字标签（颜色不是唯一提示）", () => {
    expect(IMAGE_NOTE_STATUS.set.label).toBe("已补主图");
    expect(IMAGE_NOTE_STATUS.kept.label).toBe("已有主图");
    expect(IMAGE_NOTE_STATUS.invalid.label).toBe("图片无效");
    expect(IMAGE_NOTE_STATUS.skipped.label).toBe("未找到款式");
    expect(IMAGE_NOTE_STATUS.failed.label).toBe("保存失败");
  });
});
