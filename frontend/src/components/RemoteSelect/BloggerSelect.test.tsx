// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { listBloggers } from "@/features/blogger/api";
import { BloggerSelect } from "./BloggerSelect";

vi.mock("@/features/blogger/api", () => ({ listBloggers: vi.fn() }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("打开主页不选中博主，选项文字仍可正常选择", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  vi.mocked(listBloggers).mockResolvedValue({ items: [{ id: "b1", nickname: "测试博主", xiaohongshu_id: "account", platform: "抖音", homepage_url: "https://example.com/profile" }], total: 1 } as Awaited<ReturnType<typeof listBloggers>>);
  const onChange = vi.fn();
  render(<QueryClientProvider client={client}><BloggerSelect onChange={onChange} /></QueryClientProvider>);
  fireEvent.mouseDown(screen.getByRole("combobox"));
  const link = await screen.findByRole("link", { name: /打开.*主页/ });
  expect(link.getAttribute("href")).toBe("https://example.com/profile");
  fireEvent.mouseDown(link); fireEvent.click(link);
  expect(onChange).not.toHaveBeenCalled();
  fireEvent.click(link.parentElement!.querySelector("span")!);
  await waitFor(() => expect(onChange).toHaveBeenCalledWith("b1", expect.objectContaining({ value: "b1" })));
  client.clear();
});
