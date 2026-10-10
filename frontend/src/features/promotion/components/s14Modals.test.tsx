// @vitest-environment jsdom
// 流程线 PR-2 S14：录入信息去掉颜色及规格 / 打单地址 / 发货单号；新建推广的「需要仓库发货」；改归属商品按发货状态提示（11-53、11-58）。
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Promotion } from "@/features/promotion/types";
import { ChangeGoodsModal } from "./ChangeGoodsModal";
import { CreatePromotionModal } from "./CreatePromotionModal";
import { SourceExtraModal } from "./SourceExtraModal";

vi.mock("@/features/promotion/api", () => ({}));
vi.mock("@/features/product/api", () => ({
  listSkusByStyle: () => Promise.resolve([]),
  listGoodsForStyle: () => Promise.resolve([]),
}));
vi.mock("@/components/RemoteSelect/BloggerSelect", () => ({ BloggerSelect: () => null }));
vi.mock("@/components/RemoteSelect/StyleSelect", () => ({ StyleSelect: () => null }));

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

function wrap(node: ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>;
}

function promo(over: Partial<Promotion>): Promotion {
  return {
    id: "p1",
    internal_code: "PR-001",
    style_id: "s1",
    style_code_snapshot: "ST-1",
    display_short_name: "上衣",
    style_short_name_snapshot: "上衣",
    goods_main_id: null,
    ship_status: null,
    source_extra: {},
    ...over,
  } as unknown as Promotion;
}

describe("录入信息", () => {
  it("只剩订单号 / 合作形式 / 负责PR，三个退役项不出现", async () => {
    render(
      wrap(
        <SourceExtraModal
          target={promo({
            source_extra: { 颜色及规格: "黑色 M", 打单地址: "杭州", 发货单号: "SF1", 订单号: "ORD-9" },
          })}
          onClose={() => {}}
          canManagePaymentQr={false}
        />
      )
    );
    expect(await screen.findByDisplayValue("ORD-9")).toBeTruthy();
    expect(screen.getByLabelText("订单号")).toBeTruthy();
    expect(screen.getByLabelText("负责PR")).toBeTruthy();
    expect(screen.queryByText("颜色及规格")).toBeNull();
    expect(screen.queryByText("打单地址")).toBeNull();
    expect(screen.queryByText("发货单号")).toBeNull();
    expect(screen.queryByDisplayValue("杭州")).toBeNull();
    expect(screen.queryByText(/地址/)).toBeNull();
  });
});

describe("新建推广", () => {
  it("「需要仓库发货」默认不勾，旁边有说明", async () => {
    render(wrap(<CreatePromotionModal open onClose={() => {}} />));
    const box = (await screen.findByRole("checkbox", { name: /需要仓库发货/ })) as HTMLInputElement;
    expect(box.checked).toBe(false);
    expect(screen.getByText("补录历史单不用勾；勾了才进待推送仓库")).toBeTruthy();
  });
});

describe("改归属商品", () => {
  it.each([
    [null, "换商品后，推送时要重新选颜色尺码"],
    ["待发货", "换商品后，推送时要重新选颜色尺码"],
    ["待打单", "只改报表归属，不改已发出的颜色尺码"],
    ["已发货", "只改报表归属，不改已发出的颜色尺码"],
  ] as const)("发货状态 %s → 提示「%s」", async (ship, hint) => {
    render(
      wrap(<ChangeGoodsModal target={promo({ ship_status: ship })} onClose={() => {}} />)
    );
    expect(await screen.findByText(hint)).toBeTruthy();
    const other =
      hint === "换商品后，推送时要重新选颜色尺码"
        ? "只改报表归属，不改已发出的颜色尺码"
        : "换商品后，推送时要重新选颜色尺码";
    expect(screen.queryByText(other)).toBeNull();
  });
});
