// @vitest-environment jsdom
// 结款页「导入结款」只看页级 ui.actions.import（流程线 PR-5，8.6）：财务、管理员有，主管没有；
// 上传成功的提示带上「结果去导入记录里看」。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { SettlementPage } from "./types";

const api = vi.hoisted(() => ({
  listSettlements: vi.fn(),
  uploadImportFile: vi.fn(),
}));
vi.mock("@/features/finance/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/finance/api")>()),
  listSettlements: api.listSettlements,
}));
vi.mock("@/features/import/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/features/import/api")>()),
  uploadImportFile: api.uploadImportFile,
}));

import { SettlementListPage } from "@/pages/SettlementListPage";

beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (elt: Element) => getStyle(elt);
  if (!window.matchMedia) {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => {},
        removeListener: () => {},
        addEventListener: () => {},
        removeEventListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  }
});
afterEach(cleanup);
beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
});

function renderPage(ui: SettlementPage["ui"]) {
  api.listSettlements.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, ui });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <SettlementListPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe("SettlementListPage 导入结款", () => {
  it("页级 ui 没有 import（主管）：不出「导入结款」", async () => {
    renderPage({ actions: {} });
    await waitFor(() => expect(api.listSettlements).toHaveBeenCalled());
    // 等列表响应落到页面（按钮在响应之后才判）
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(screen.queryByRole("button", { name: /导入结款/ })).toBeNull();
  });

  it("页级 ui 有 import（财务、管理员）：出按钮，上传成功提示去导入记录看结果", async () => {
    api.uploadImportFile.mockResolvedValue({ batch_id: "b1" });
    renderPage({ actions: { import: { state: "enabled" } } });
    expect(await screen.findByRole("button", { name: /导入结款/ })).toBeTruthy();
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(["a,b"], "结款.csv", { type: "text/csv" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(api.uploadImportFile).toHaveBeenCalledWith("manual_settlement", file));
    expect(await screen.findByText(/结果在「系统管理 → 导入记录」里看/)).toBeTruthy();
  });
});
