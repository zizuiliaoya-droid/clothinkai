// @vitest-environment jsdom
// BI 看板（7e R1）：BI 改挂 report.bi:read 后，加载失败的提示要指向 BI 看板权限，不再是投产报表权限。
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const biMock = vi.fn();
vi.mock("@/features/report/api", () => ({
  getBiDashboard: (...args: unknown[]) => biMock(...args),
}));
vi.mock("@/features/preference/api", () => ({
  getFilterPreference: () => Promise.resolve({}),
  saveFilterPreference: () => Promise.resolve(undefined),
}));

import { BiDashboardPage } from "./BiDashboardPage";

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

beforeEach(() => {
  biMock.mockReset();
});
afterEach(cleanup);

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/bi"]}>
        <BiDashboardPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("BiDashboardPage 加载失败", () => {
  it("提示检查 BI 看板权限，不再提投产报表权限", async () => {
    biMock.mockRejectedValue(
      Object.assign(new Error("403"), {
        response: { status: 403, data: { code: "PERMISSION_DENIED", message: "没有权限" } },
      })
    );
    renderPage();
    expect(await screen.findByText("BI 数据加载失败")).toBeTruthy();
    expect(screen.getByText(/BI 看板权限/)).toBeTruthy();
    expect(screen.queryByText(/投产报表权限/)).toBeNull();
  });
});