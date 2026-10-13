// @vitest-environment jsdom
// 旧路由 /store-daily → /store/daily（7e R4 S6）：查询串原样带过去（旧书签、别人发来的链接不丢筛选）。
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import { LegacyStoreRedirect } from "./LegacyStoreRedirect";

afterEach(cleanup);

function Target() {
  const location = useLocation();
  return <div data-testid="target">{`${location.pathname}${location.search}`}</div>;
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/store-daily" element={<LegacyStoreRedirect />} />
        <Route path="/store/daily" element={<Target />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("LegacyStoreRedirect", () => {
  it("带查询串：原样带到 /store/daily", () => {
    renderAt("/store-daily?preset=custom&date_from=2026-09-01&date_to=2026-09-30");
    expect(screen.getByTestId("target").textContent).toBe(
      "/store/daily?preset=custom&date_from=2026-09-01&date_to=2026-09-30"
    );
  });

  it("不带查询串：只换路径", () => {
    renderAt("/store-daily");
    expect(screen.getByTestId("target").textContent).toBe("/store/daily");
  });
});
