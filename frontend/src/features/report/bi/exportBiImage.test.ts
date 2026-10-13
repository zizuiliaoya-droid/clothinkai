// @vitest-environment jsdom
// 7e R6 S10：导出图片的纯函数与节点还原（html-to-image 已 mock）。
import { afterEach, describe, expect, it, vi } from "vitest";

const toPngMock = vi.fn();
vi.mock("html-to-image", () => ({
  toPng: (...args: unknown[]) => toPngMock(...args),
}));

import { biImageFilename, exportBiImage, watermarkText, WATERMARK_ATTR } from "./exportBiImage";

afterEach(() => {
  toPngMock.mockReset();
  vi.restoreAllMocks();
  document.body.innerHTML = "";
});

describe("watermarkText", () => {
  const at = new Date(2026, 9, 13, 9, 5, 42);

  it("登录名 姓名 YYYY-MM-DD HH:mm", () => {
    expect(watermarkText({ username: "zhang", display_name: "张三" }, at)).toBe("zhang 张三 2026-10-13 09:05");
  });

  it("没填姓名用登录名", () => {
    expect(watermarkText({ username: "wang", display_name: null }, at)).toBe("wang wang 2026-10-13 09:05");
    expect(watermarkText({ username: "wang", display_name: "" }, at)).toBe("wang wang 2026-10-13 09:05");
  });
});

describe("biImageFilename", () => {
  it("bi-<tab>_<起>_<止>.png", () => {
    expect(biImageFilename("goods", "2026-09-13", "2026-10-12")).toBe("bi-goods_2026-09-13_2026-10-12.png");
  });
});

describe("exportBiImage", () => {
  it("截图时节点是 relative、带水印；截完还原 position、移除水印；失败照样还原并抛出", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const node = document.createElement("div");
    document.body.appendChild(node);
    const seen: Array<{ position: string; layers: number }> = [];
    toPngMock.mockImplementation((n: HTMLElement) => {
      seen.push({ position: n.style.position, layers: n.querySelectorAll(`[${WATERMARK_ATTR}]`).length });
      return Promise.resolve("data:image/png;base64,AAAA");
    });

    await exportBiImage(node, "zhang 张三 2026-10-13 09:05", "bi-store_a_b.png");
    expect(seen).toEqual([{ position: "relative", layers: 1 }]);
    expect(node.style.position).toBe("");
    expect(node.querySelector(`[${WATERMARK_ATTR}]`)).toBeNull();
    // 下载用的临时链接也已移除
    expect(document.querySelector("a")).toBeNull();

    toPngMock.mockRejectedValue(new Error("boom"));
    await expect(exportBiImage(node, "t", "x.png")).rejects.toThrow("boom");
    expect(node.style.position).toBe("");
    expect(node.querySelector(`[${WATERMARK_ATTR}]`)).toBeNull();
  });
});
