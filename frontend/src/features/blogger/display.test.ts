import { describe, expect, it } from "vitest";
import { missingTagRowsText, safeHomepageUrl } from "./display";

describe("safeHomepageUrl", () => {
  it("只放行 http / https，scheme 不区分大小写（后端能存 HTTP://）", () => {
    expect(safeHomepageUrl("https://example.com/u/1")).toBe("https://example.com/u/1");
    expect(safeHomepageUrl("http://example.com")).toBe("http://example.com");
    expect(safeHomepageUrl("HTTP://example.com/A")).toBe("HTTP://example.com/A");
    expect(safeHomepageUrl("Https://example.com")).toBe("Https://example.com");
  });

  it("其余 scheme 与空值不渲染成链接", () => {
    expect(safeHomepageUrl("javascript:alert(1)")).toBeNull();
    expect(safeHomepageUrl("ftp://example.com")).toBeNull();
    expect(safeHomepageUrl("example.com")).toBeNull();
    expect(safeHomepageUrl("")).toBeNull();
    expect(safeHomepageUrl("   ")).toBeNull();
    expect(safeHomepageUrl(null)).toBeNull();
    expect(safeHomepageUrl(undefined)).toBeNull();
  });
});

describe("missingTagRowsText", () => {
  it("写明是数据行序号，并给出换算成 Excel 行号的办法", () => {
    expect(missingTagRowsText([3, 5], 2)).toBe(
      "数据第 3、5 行（Excel 行号 = 数据行号 + 表头所在行号，表头在第 1 行就加 1）"
    );
  });

  it("次数多于给出的行号（后端只给前 20 个）时末尾带「等」", () => {
    expect(missingTagRowsText([1, 2], 7)).toBe(
      "数据第 1、2 行等（Excel 行号 = 数据行号 + 表头所在行号，表头在第 1 行就加 1）"
    );
    expect(missingTagRowsText([1, 2], 2)).not.toContain("行等");
  });

  it("没有行号 → 空串", () => {
    expect(missingTagRowsText([], 0)).toBe("");
  });
});
