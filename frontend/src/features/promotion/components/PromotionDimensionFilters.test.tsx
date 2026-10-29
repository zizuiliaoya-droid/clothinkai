// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { PromotionFilterOptions } from "../api";
import { PromotionDimensionFilters } from "./PromotionDimensionFilters";

const getOptions = vi.hoisted(() => vi.fn());
vi.mock("../api", () => ({ getPromotionFilterOptions: getOptions }));

const ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const selected = { id: ID, display_name: "已选春季商品" };
const result = (name: string): PromotionFilterOptions => ({
  items: [{ id: name, display_name: name }], selected,
  total: 80, page: 1, page_size: 50, seasons: ["春季"],
});

function mount() {
  const onChange = vi.fn();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <PromotionDimensionFilters value={{ goods_id: ID, season: ["春季"] }} onChange={onChange} />
  </QueryClientProvider>);
  return onChange;
}

afterEach(() => { cleanup(); getOptions.mockReset(); });

describe("推广限定商品选项", () => {
  it("跨页搜索保持已选回显，搜索重置页码且限制长度", async () => {
    getOptions.mockImplementation(({ page }: { page: number }) => Promise.resolve(result(`选项页${page}`)));
    const onChange = mount();
    await screen.findByText(selected.display_name);
    const input = screen.getByRole("combobox", { name: "商品筛选" });
    fireEvent.mouseDown(input);
    fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
    await waitFor(() => expect(getOptions).toHaveBeenLastCalledWith({ page: 2, keyword: undefined, selected_id: ID }));
    fireEvent.change(input, { target: { value: "长".repeat(80) } });
    await waitFor(() => expect(getOptions).toHaveBeenLastCalledWith({ page: 1, keyword: "长".repeat(64), selected_id: ID }));
    expect(screen.getAllByText(selected.display_name).length).toBeGreaterThan(0);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("较慢的旧搜索不覆盖新选项，也不清空选择", async () => {
    let resolveOld!: (value: PromotionFilterOptions) => void;
    getOptions.mockImplementation(({ keyword }: { keyword?: string }) => keyword === "旧"
      ? new Promise<PromotionFilterOptions>((resolve) => { resolveOld = resolve; })
      : Promise.resolve(result(keyword || "初始")));
    const onChange = mount();
    await screen.findByText(selected.display_name);
    const input = screen.getByRole("combobox", { name: "商品筛选" });
    fireEvent.mouseDown(input);
    fireEvent.change(input, { target: { value: "旧" } });
    await waitFor(() => expect(resolveOld).toBeDefined());
    fireEvent.change(input, { target: { value: "新" } });
    await screen.findAllByText("新");
    await act(async () => { resolveOld(result("已过期选项")); });
    await waitFor(() => expect(screen.queryByText("已过期选项")).toBeNull());
    expect(screen.getAllByText(selected.display_name).length).toBeGreaterThan(0);
    expect(onChange).not.toHaveBeenCalled();
  });
});
