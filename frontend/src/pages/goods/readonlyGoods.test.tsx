// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, expect, it, vi } from "vitest";
import { GoodsPanel } from "./GoodsPanel";
import { listGoods, getGoodsSeasonOptions, type Goods } from "@/features/product/api";
import { useSupportStore } from "@/stores/supportStore";

vi.mock("@/features/product/api", async (original) => ({ ...await original<typeof import("@/features/product/api")>(), listGoods: vi.fn(), getGoodsSeasonOptions: vi.fn() }));
vi.mock("./StyleEditModal", () => ({ StyleEditModal: () => null }));
vi.mock("./SuitLinkModal", () => ({ SuitLinkModal: () => null }));
beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (element: Element) => getStyle(element);
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({ matches: false, media: query, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return false; } }));
});
afterEach(() => { cleanup(); useSupportStore.getState().end(); });

it("商品只读模式隐藏新建/编辑/删除和款式编辑链接，搜索与分页仍可用", async () => {
  const goods: Goods = { id: "g", goods_code: "g01", goods_title: "秋冬外套", short_name: null, season: "秋冬", brand_id: null, brand_name: null, remark: null, images: [], is_suit: false, is_active: true, created_at: "2026-10-31", updated_at: "2026-10-31", items: [{ id: "item-1", style_id: "s", style_code: "S01", style_name: "外套款", single_goods_cost: "100", sort_order: 0, is_active: true }], total_cost: "100", cost_missing_count: 0, link_count: 0, notices: [] };
  vi.mocked(listGoods).mockImplementation(async (filters) => ({ items: [goods], total: 40, page: filters?.page ?? 1, page_size: 20 }));
  vi.mocked(getGoodsSeasonOptions).mockResolvedValue(["秋冬"]);
  useSupportStore.getState().start({ access_token: "support", expires_in: 900, tenant: { id: "a", code: "a", name: "客户 A" } });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><GoodsPanel /></QueryClientProvider>);
  expect(await screen.findByText("秋冬外套")).toBeTruthy();
  expect(screen.getByText("S01").closest("button")).toBeNull();
  for (const name of ["新建商品", "编辑", "删除", "绑定链接"]) expect(screen.queryByRole("button", { name })).toBeNull();
  const search = screen.getByPlaceholderText("商品编码 / 简称 / 全称 / 成员货号 / 款名");
  fireEvent.change(search, { target: { value: "外套" } });
  fireEvent.keyDown(search, { key: "Enter", code: "Enter", keyCode: 13 });
  await waitFor(() => expect(listGoods).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: "外套", page: 1 })));
  fireEvent.click(await screen.findByTitle("2"));
  await waitFor(() => expect(listGoods).toHaveBeenLastCalledWith(expect.objectContaining({ keyword: "外套", page: 2 })));
});
