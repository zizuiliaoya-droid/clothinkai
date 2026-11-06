// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { downloadImportTemplate } from "@/features/import/api";
import { ImportUploadButton } from "./ImportUploadButton";

vi.mock("@/features/import/api", () => ({
  downloadImportTemplate: vi.fn(),
  uploadImportFile: vi.fn(),
}));

function show(source: string) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <ImportUploadButton source={source} />
    </QueryClientProvider>
  );
}

describe("空白导入模板", () => {
  beforeEach(() => { vi.clearAllMocks(); });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it.each(["manual_blogger", "manual_promotion"])("%s 可下载对应来源的文件", async (source) => {
    vi.mocked(downloadImportTemplate).mockResolvedValue(new Blob(["xlsx"]));
    const create = vi.fn().mockReturnValue("blob:template");
    const revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    show(source);
    fireEvent.click(screen.getByRole("button", { name: /下载空模板/ }));
    await waitFor(() => expect(downloadImportTemplate).toHaveBeenCalledWith(source));
    await waitFor(() => expect(click).toHaveBeenCalledOnce());
    expect(revoke).toHaveBeenCalledWith("blob:template");
    click.mockRestore();
  });

  it("第三方原文件来源不提供人工模板", () => {
    show("huitun_douyin");
    expect(screen.queryByRole("button", { name: /下载空模板/ })).toBeNull();
  });

  it("下载失败显示错误并恢复按钮", async () => {
    vi.mocked(downloadImportTemplate).mockRejectedValue(new Error("没有模板下载权限"));
    show("manual_blogger");
    fireEvent.click(screen.getByRole("button", { name: /下载空模板/ }));
    await waitFor(() => expect(screen.getByText("没有模板下载权限")).toBeTruthy());
    expect(screen.getByRole("button", { name: /下载空模板/ })).not.toHaveClass("ant-btn-loading");
  });
});

