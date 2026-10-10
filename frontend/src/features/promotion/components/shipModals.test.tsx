// @vitest-environment jsdom
// 发货 4 个弹窗：按 target 出成员行、缺项、旧单预选与提示文字。
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Promotion } from "@/features/promotion/types";
import { ItemsModal } from "./ItemsModal";
import { ReceiverModal } from "./ReceiverModal";
import { ShipPushModal } from "./ShipPushModal";
import { ShipWithdrawModal } from "./ShipWithdrawModal";

vi.mock("@/features/promotion/api", () => ({}));
vi.mock("@/features/product/api", () => ({
  listSkusByStyle: (styleId: string) =>
    Promise.resolve(
      styleId === "s1"
        ? [
            { id: "k1", style_id: "s1", sku_code: "SKU-1", color: "黑色", size: "M" },
            { id: "k2", style_id: "s1", sku_code: "SKU-2", color: "黑色", size: "L" },
          ]
        : [{ id: "k3", style_id: styleId, sku_code: "SKU-3", color: "白色", size: "S" }]
    ),
}));

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
    display_short_name: "上衣",
    style_short_name_snapshot: "上衣",
    items: [],
    legacy_color_spec: null,
    goods_members: [{ style_id: "s1", display_short_name: "上衣", goods_title: "条纹上衣" }],
    receiver_name: null,
    receiver_phone: null,
    receiver_address: null,
    ship_status: "待发货",
    source_extra: {},
    ui: null,
    ...over,
  } as unknown as Promotion;
}

describe("ShipPushModal", () => {
  it("套装没有明细：每个成员一行，顶部列出缺项，收件预填", async () => {
    render(
      wrap(
        <ShipPushModal
          target={{
            row: promo({
              receiver_name: "张三",
              goods_members: [
                { style_id: "s1", display_short_name: "上衣", goods_title: "条纹上衣" },
                { style_id: "s2", display_short_name: "阔腿裤", goods_title: "阔腿裤" },
              ],
            }),
            action: {
              state: "enabled",
              missing: [
                { key: "receiver_phone", label: "收件电话" },
                { key: "goods_items", label: "颜色尺码" },
              ],
            },
          }}
          onClose={() => {}}
        />
      )
    );
    expect(await screen.findByText("还缺：收件电话、颜色尺码")).toBeTruthy();
    expect(screen.getByDisplayValue("张三")).toBeTruthy();
    expect(screen.getByText("颜色尺码 · 上衣")).toBeTruthy();
    expect(screen.getByText("颜色尺码 · 阔腿裤")).toBeTruthy();
  });

  it("旧单：原文对上 SKU 就预选并提示核对，不显示 SKU 编码", async () => {
    render(
      wrap(
        <ShipPushModal
          target={{
            row: promo({ legacy_color_spec: " 黑色  M " }),
            action: { state: "enabled", missing: [{ key: "goods_items", label: "颜色尺码" }] },
          }}
          onClose={() => {}}
        />
      )
    );
    expect(await screen.findByText("按录入信息预选，请核对")).toBeTruthy();
    expect(screen.getByText("黑色 / M")).toBeTruthy();
    expect(screen.queryByText(/SKU-/)).toBeNull();
  });
});

describe("ItemsModal", () => {
  it("已有明细：带出当前颜色尺码", async () => {
    render(
      wrap(
        <ItemsModal
          target={promo({
            items: [
              {
                style_id: "s1",
                sku_id: "k2",
                display_short_name: "上衣",
                goods_title: "条纹上衣",
                color: "黑色",
                size: "L",
                style_main_image_url: null,
              },
            ],
          })}
          onClose={() => {}}
        />
      )
    );
    expect(await screen.findByText("黑色 / L")).toBeTruthy();
    expect(screen.queryByText("按录入信息预选，请核对")).toBeNull();
  });
});

describe("ReceiverModal / ShipWithdrawModal", () => {
  it("待打单改收件：顶部提示要告知仓库；待发货不提示", async () => {
    const { rerender } = render(
      wrap(<ReceiverModal target={promo({ ship_status: "待打单", receiver_name: "李四" })} onClose={() => {}} />)
    );
    expect(await screen.findByDisplayValue("李四")).toBeTruthy();
    expect(screen.getByText(/改完请告知仓库/)).toBeTruthy();
    rerender(wrap(<ReceiverModal target={null} onClose={() => {}} />));
    rerender(wrap(<ReceiverModal target={promo({ id: "p2", receiver_name: "王五" })} onClose={() => {}} />));
    expect(await screen.findByDisplayValue("王五")).toBeTruthy();
    expect(screen.queryByText(/改完请告知仓库/)).toBeNull();
  });

  it("撤回推送：提示仓库待打单里会消失", async () => {
    render(wrap(<ShipWithdrawModal target={promo({ ship_status: "待打单" })} onClose={() => {}} />));
    expect(await screen.findByText("仓库的待打单里会消失这一单")).toBeTruthy();
  });
});
