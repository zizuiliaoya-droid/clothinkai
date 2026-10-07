// 8a-1 护栏（AC 4、AC 2 的字典部分）：款式维护已并进商品 / 套装页，源码里不再出现旧菜单名；
// 字典弹窗不再有类目分组。用 import.meta.glob 读源码原文（仓库没有 @types/node，不用 node:fs）。
import { describe, expect, it } from "vitest";

const sources = import.meta.glob<string>(
  ["/src/**/*.{ts,tsx}", "!/src/**/*.test.{ts,tsx}"],
  { query: "?raw", import: "default", eager: true }
);

// 关键字拼出来：排除模式万一失效、读到本文件，也不会自己命中自己
const LEGACY_MENU_NAME = "款式" + "管理";

describe("旧「款式维护页」字样", () => {
  it("读到了源码且不含测试文件（防空跑）", () => {
    const paths = Object.keys(sources);
    expect(paths).toContain("/src/App.tsx");
    expect(paths.filter((p) => p.includes(".test."))).toEqual([]);
  });

  it("前端源码里没有旧菜单名", () => {
    const hits = Object.entries(sources)
      .filter(([, text]) => text.includes(LEGACY_MENU_NAME))
      .map(([path]) => path);
    expect(hits).toEqual([]);
  });

  it("字典弹窗不再有类目分组", () => {
    const dictModal = sources["/src/components/DictManager/DictManagerModal.tsx"];
    expect(dictModal).toBeTypeOf("string");
    expect(dictModal).not.toContain("类目");
  });
});
