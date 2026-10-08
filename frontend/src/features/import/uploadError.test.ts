import { describe, expect, it } from "vitest";
import { importUploadErrorMessage } from "./uploadError";

const HINT = "含内嵌图片的聚水潭导出请分批导出";

function apiError(status: number, code: string, message: string) {
  return { response: { status, data: { code, message } } };
}

describe("importUploadErrorMessage", () => {
  it("后端 422 IMPORT_FILE_TOO_LARGE：原文案后接提示", () => {
    expect(
      importUploadErrorMessage(apiError(422, "IMPORT_FILE_TOO_LARGE", "文件超过大小上限"), HINT)
    ).toBe("文件超过大小上限；含内嵌图片的聚水潭导出请分批导出");
  });

  it("nginx 先挡的 413（无 JSON 体）：基文案 + 提示", () => {
    const err = { response: { status: 413, data: "<html>413 Request Entity Too Large</html>" } };
    expect(importUploadErrorMessage(err, HINT)).toBe(
      "文件超过大小上限；含内嵌图片的聚水潭导出请分批导出"
    );
  });

  it("其他错误不追加提示", () => {
    expect(
      importUploadErrorMessage(apiError(409, "IMPORT_DUPLICATE_FILE", "文件已导入过"), HINT)
    ).toBe("文件已导入过");
    expect(importUploadErrorMessage(new Error("网络错误"), HINT)).toBe("网络错误");
  });

  it("不传提示时超限只显示原文案", () => {
    expect(
      importUploadErrorMessage(apiError(422, "IMPORT_FILE_TOO_LARGE", "文件超过大小上限"))
    ).toBe("文件超过大小上限");
    expect(importUploadErrorMessage({ response: { status: 413, data: "" } })).toBe(
      "文件超过大小上限"
    );
  });
});
