import { describe, expect, it } from "vitest";
import {
  chunk,
  findDuplicateStems,
  hasAllowedExtension,
  imageStem,
  isRequestTimeout,
  missingResults,
} from "./imageBatch";

// 与 backend/tests/unit/test_style_images.py 的 IMAGE_STEM_CASES 同一张表
const IMAGE_STEM_CASES: [string, string][] = [
  ["a/b/ABC.jpg", "ABC"],
  ["C:\\x\\abc.PNG", "abc"],
  [" 2025945 .webp", "2025945"],
  ["x.tar.gz", "x.tar"],
  ["2025946", "2025946"],
  ["  款A01.jpeg ", "款A01"],
  [".jpg", ".jpg"],
  ["dir/", ""],
  ["", ""],
];

describe("imageStem", () => {
  it.each(IMAGE_STEM_CASES)("%j → %j（与后端 image_stem 同口径）", (filename, stem) => {
    expect(imageStem(filename)).toBe(stem);
  });
});

describe("hasAllowedExtension", () => {
  it("只收 jpg / jpeg / png / webp，不区分大小写", () => {
    expect(["a.JPG", "a.jpeg", "a.Png", "a.webp"].map(hasAllowedExtension)).toEqual([
      true,
      true,
      true,
      true,
    ]);
    expect(["a.gif", "a.heic", "a", "a.jpg.txt"].map(hasAllowedExtension)).toEqual([
      false,
      false,
      false,
      false,
    ]);
  });
});

describe("findDuplicateStems（N14：按整次选择判重）", () => {
  it("ABC.jpg 与 abc.png 分在两批也都被找出", () => {
    const files = [
      { name: "ABC.jpg" },
      ...Array.from({ length: 10 }, (_, i) => ({ name: `F${i}.jpg` })),
      { name: "abc.png" },
    ];
    // 按 10 张一批切开后两张不在同一批，整次选择仍能找出
    const batches = chunk(files, 10);
    expect(batches[0].map((f) => f.name)).toContain("ABC.jpg");
    expect(batches[1].map((f) => f.name)).toContain("abc.png");
    expect([...findDuplicateStems(files)].sort()).toEqual(["ABC.jpg", "abc.png"]);
  });

  it("目录与空白不同也算重名；没有重名返回空；空 stem 不参与", () => {
    expect([...findDuplicateStems([{ name: "x/A1.jpg" }, { name: " a1 .webp" }])].sort()).toEqual([
      " a1 .webp",
      "x/A1.jpg",
    ]);
    expect(findDuplicateStems([{ name: "A1.jpg" }, { name: "A2.jpg" }]).size).toBe(0);
    expect(findDuplicateStems([{ name: "" }, { name: "" }]).size).toBe(0);
  });
});

describe("chunk", () => {
  it("每 10 张一批，最后一批可以不足", () => {
    const list = Array.from({ length: 23 }, (_, i) => i);
    expect(chunk(list, 10).map((c) => c.length)).toEqual([10, 10, 3]);
    expect(chunk([], 10)).toEqual([]);
  });

  it("size 非正数直接报错", () => {
    expect(() => chunk([1], 0)).toThrow();
  });
});

describe("missingResults（N14：结果对账）", () => {
  it("返回发出去了但结果里没有的文件名", () => {
    expect(
      missingResults(["a.jpg", "b.jpg", "c.jpg"], [{ filename: "a.jpg" }, { filename: "c.jpg" }])
    ).toEqual(["b.jpg"]);
  });

  it("齐全时为空；同名按次数核对", () => {
    expect(missingResults(["a.jpg"], [{ filename: "a.jpg" }])).toEqual([]);
    expect(missingResults(["a.jpg", "a.jpg"], [{ filename: "a.jpg" }])).toEqual(["a.jpg"]);
  });
});

describe("isRequestTimeout（N22）", () => {
  it("ECONNABORTED / ETIMEDOUT 且没有 response → 超时", () => {
    expect(isRequestTimeout({ code: "ECONNABORTED" })).toBe(true);
    expect(isRequestTimeout({ code: "ETIMEDOUT" })).toBe(true);
  });

  it("带 response 的 4xx 与其他错误都不是超时", () => {
    expect(isRequestTimeout({ code: "ERR_BAD_REQUEST", response: { status: 413 } })).toBe(false);
    expect(isRequestTimeout({ code: "ECONNABORTED", response: { status: 422 } })).toBe(false);
    expect(isRequestTimeout({ code: "ERR_NETWORK" })).toBe(false);
    expect(isRequestTimeout(new Error("boom"))).toBe(false);
    expect(isRequestTimeout(null)).toBe(false);
    expect(isRequestTimeout("ECONNABORTED")).toBe(false);
  });
});
