// @vitest-environment jsdom
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PlatformPostsEditor } from "./PlatformPostsEditor";
import type { Promotion } from "../types";

const save = vi.hoisted(() => vi.fn());
vi.mock("../api", () => ({ replacePlatformPosts: save }));

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", { writable: true, value: () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} }) });
  const style = window.getComputedStyle.bind(window);
  window.getComputedStyle = (el) => style(el);
});
afterEach(() => { cleanup(); save.mockReset(); });

function mount(state: "read" | "edit", count = 1) {
  const promotion = { id: "promotion-id", platform: "小红书", ui: { fields: { platform_posts: { state } } },
    platform_posts: Array.from({ length: count }, (_, i) => ({ id: String(i), platform: i ? `历史平台${i}` : "抖音", publish_url: "https://example.com/post", actual_publish_date: "2026-10-20", like_count: 9 })) } as unknown as Promotion;
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}><PlatformPostsEditor promotion={promotion} /></QueryClientProvider>);
}

describe("其他平台录入", () => {
  it("只读显示独立链接和点赞，不出现编辑", () => {
    mount("read");
    expect(screen.getByText("点赞：9")).toBeTruthy();
    expect(screen.getByRole("link").getAttribute("href")).toBe("https://example.com/post");
    expect(screen.queryByRole("button", { name: "编辑其他平台" })).toBeNull();
  });
  it("删除最后一行以空数组保存，刷新详情", async () => {
    save.mockResolvedValue({});
    mount("edit");
    fireEvent.click(screen.getByRole("button", { name: "编辑其他平台" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "删除平台1" }));
    fireEvent.click(within(dialog).getByRole("button", { name: /保.*存/ }));
    await waitFor(() => expect(save).toHaveBeenCalledWith("promotion-id", []));
  });
  it("五条时禁用增加，删除后恢复", async () => {
    mount("edit", 5);
    fireEvent.click(screen.getByRole("button", { name: "编辑其他平台" }));
    const dialog = await screen.findByRole("dialog");
    expect((within(dialog).getByRole("button", { name: "添加平台" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(within(dialog).getByRole("button", { name: "删除平台5" }));
    expect((within(dialog).getByRole("button", { name: "添加平台" }) as HTMLButtonElement).disabled).toBe(false);
  });
});
