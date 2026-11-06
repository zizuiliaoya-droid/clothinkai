// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({ support: false, get: vi.fn(), post: vi.fn() }));
vi.mock("@/services/apiClient", () => ({ apiClient: { get: state.get, post: state.post } }));
vi.mock("@/stores/supportStore", () => ({ useReadOnlySupport: () => state.support }));
import { NotificationBell } from "./NotificationBell";

function Location() { const loc = useLocation(); return <output data-testid="location">{loc.pathname}{loc.search}</output>; }
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<MemoryRouter><QueryClientProvider client={client}><NotificationBell /><Location /></QueryClientProvider></MemoryRouter>);
}
beforeEach(() => { state.support = false; state.get.mockReset(); state.post.mockReset(); });
beforeAll(() => {
  Object.defineProperty(window, "matchMedia", { writable: true, value: (query: string) => ({
    matches: false, media: query, onchange: null, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  }) });
});
afterEach(cleanup);

describe("顶栏站内通知", () => {
  it("留言通知只跳转，实际展示时间线后才由抽屉标读", async () => {
    state.get.mockImplementation(async (url: string) => ({ data: url.endsWith("unread-count") ? { count: 1 } : [
      { id: "notice1", type: "line_comment", content: "有新留言", link: "/promotions?id=p1", is_read: false, created_at: "2026-11-05T08:00:00Z" },
    ] }));
    mount();
    fireEvent.click(screen.getByRole("button", { name: "通知" }));
    fireEvent.click(await screen.findByRole("button", { name: "查看" }));
    expect(screen.getByTestId("location").textContent).toBe("/promotions?id=p1");
    expect(state.post).not.toHaveBeenCalled();
  });
  it("每日汇总能进入谈款或推广待跟进列表", async () => {
    state.get.mockImplementation(async (url: string) => ({ data: url.endsWith("unread-count") ? { count: 1 } : [
      { id: "summary1", type: "follow_up_summary", content: "有合作事项超过120小时未跟进", link: "/promotions?needs_follow_up=true", is_read: false },
    ] }));
    mount(); fireEvent.click(screen.getByRole("button", { name: "通知" }));
    expect(await screen.findByRole("button", { name: "推广待跟进" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "谈款待跟进" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/negotiations?needs_follow_up=true"));
  });
  it("support不出现通知入口也不读取客户通知", () => {
    state.support = true; mount();
    expect(screen.queryByRole("button", { name: "通知" })).toBeNull();
    expect(state.get).not.toHaveBeenCalled(); expect(state.post).not.toHaveBeenCalled();
  });
});
