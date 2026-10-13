// @vitest-environment jsdom
// 发文进度表（7e R4 S7，R1 遗留）：没有读权限、直接打开 URL 时接口 403 →
// 整页显示「没有权限查看发文进度表」，不显示全 0 的统计卡；有权限时统计卡照常。
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const summaryMock = vi.fn();
const cardsMock = vi.fn();
vi.mock("@/features/report/api", () => ({
  getPublishSummary: (...args: unknown[]) => summaryMock(...args),
  getPublishCards: (...args: unknown[]) => cardsMock(...args),
}));

import { PublishProgressPage } from "./PublishProgressPage";

const FORBIDDEN = Object.assign(new Error("403"), {
  response: { status: 403, data: { code: "PERMISSION_DENIED" } },
});

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
  summaryMock.mockReset();
  cardsMock.mockReset();
});
afterEach(cleanup);

function renderPage() {
  // 页面自己的 retry 规则（403 不重试）要生效，这里不关全局重试
  const client = new QueryClient();
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/publish-progress"]}>
        <PublishProgressPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

describe("PublishProgressPage 无权限", () => {
  it("接口 403：显示「没有权限查看发文进度表」，没有统计卡，不重试", async () => {
    summaryMock.mockRejectedValue(FORBIDDEN);
    cardsMock.mockRejectedValue(FORBIDDEN);
    renderPage();
    expect(await screen.findByText("没有权限查看发文进度表")).toBeTruthy();
    expect(screen.queryByText("约篇量")).toBeNull();
    expect(screen.queryByText("发布率")).toBeNull();
    expect(summaryMock).toHaveBeenCalledTimes(1);
    expect(cardsMock).toHaveBeenCalledTimes(1);
  });

  it("有权限：统计卡照常，没有无权限提示", async () => {
    summaryMock.mockResolvedValue({
      quote_count: 3,
      quote_amount: "300.00",
      publish_count: 2,
      publish_rate: "0.6667",
      like_count: 10,
      cancel_count: 0,
    });
    cardsMock.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 12 });
    renderPage();
    expect((await screen.findAllByText("约篇量")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("发布率").length).toBeGreaterThan(0);
    expect(screen.queryByText(/没有权限查看/)).toBeNull();
  });
});
