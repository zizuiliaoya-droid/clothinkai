// @vitest-environment jsdom
// 弹窗从列表页拆出来后，打开时的表单初值改由弹窗自己按 target 填：这里钉住「打开就带出原值、换一条单就换值」。
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import dayjs from "dayjs";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { Promotion } from "@/features/promotion/types";
import { PublishModal } from "./PublishModal";
import { ResubmitModal } from "./ResubmitModal";
import { ReturnWaybillModal } from "./ReturnWaybillModal";
import { SourceExtraModal } from "./SourceExtraModal";

vi.mock("@/features/promotion/api", () => ({}));
vi.mock("@/features/product/api", () => ({
  listSkusByStyle: () => Promise.resolve([]),
}));

beforeAll(() => {
  // Modal 量滚动条宽度时带伪元素参数调 getComputedStyle，jsdom 没实现这个参数，只会刷屏
  const getStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (elt: Element) => getStyle(elt);
  // antd 的响应式工具要 matchMedia，jsdom 没有
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
  const qc = new QueryClient();
  return <QueryClientProvider client={qc}>{node}</QueryClientProvider>;
}

function promo(over: Partial<Promotion>): Promotion {
  return {
    id: "p1",
    internal_code: "PR-001",
    style_id: "s1",
    source_extra: {},
    ...over,
  } as unknown as Promotion;
}

describe("拆出的弹窗按 target 填初值", () => {
  it("寄回单号：打开带出原单号，换一条单换成它的", async () => {
    const { rerender } = render(
      wrap(<ReturnWaybillModal target={promo({ return_waybill: "SF001" })} onClose={() => {}} />)
    );
    expect(await screen.findByDisplayValue("SF001")).toBeTruthy();
    rerender(wrap(<ReturnWaybillModal target={null} onClose={() => {}} />));
    rerender(
      wrap(
        <ReturnWaybillModal
          target={promo({ id: "p2", return_waybill: "YT002" })}
          onClose={() => {}}
        />
      )
    );
    expect(await screen.findByDisplayValue("YT002")).toBeTruthy();
  });

  it("录入信息：带出 source_extra 里的人工源列", async () => {
    render(
      wrap(
        <SourceExtraModal
          target={promo({ source_extra: { 订单号: "ORD-9", 负责PR: "小王" } })}
          onClose={() => {}}
          canManagePaymentQr={false}
        />
      )
    );
    expect(await screen.findByDisplayValue("ORD-9")).toBeTruthy();
    expect(screen.getByDisplayValue("小王")).toBeTruthy();
  });

  it("重新提交：预填当前发布链接", async () => {
    render(
      wrap(
        <ResubmitModal
          target={promo({
            publish_url: "https://www.xiaohongshu.com/x",
            review_reason_category: "延迟发文",
          })}
          onClose={() => {}}
        />
      )
    );
    expect(await screen.findByDisplayValue("https://www.xiaohongshu.com/x")).toBeTruthy();
  });

  it("标记发布：打开时实际发布日期默认今天", async () => {
    render(
      wrap(
        <PublishModal open target={promo({})} onCancel={() => {}} onDone={() => {}} />
      )
    );
    expect(await screen.findByDisplayValue(dayjs().format("YYYY-MM-DD"))).toBeTruthy();
  });
});
