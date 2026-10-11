// @vitest-environment jsdom
// 报表下钻弹窗（7e R1）：请求参数（未分配传 unassigned）、金额 null 显示「—」、不渲染链接、翻页。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { getDrillPromotions } from "@/features/report/api";
import type { DrillPromotionPage, DrillPromotionRow } from "@/features/report/types";
import {
  DRILL_PAGE_SIZE,
  drillPromotionParams,
  type DrillPromotionTarget,
} from "@/features/report/drill";
import { DrillPromotionModal } from "./DrillPromotionModal";

vi.mock("@/features/report/api", () => ({ getDrillPromotions: vi.fn() }));

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

const PR1 = "11111111-1111-4111-8111-111111111111";

function row(over: Partial<DrillPromotionRow>): DrillPromotionRow {
  return {
    id: "p1",
    internal_code: "TG-001",
    cooperation_date: "2026-10-03",
    scheduled_publish_date: "2026-10-10",
    actual_publish_date: null,
    pr_id: PR1,
    pr_name: "小王",
    blogger_nickname: "博主甲",
    platform: "小红书",
    display_short_name: "条纹上衣",
    publish_status: "未发布",
    recall_status: "无需召回",
    quote_amount: "300.00",
    total_promo_cost: "420.50",
    like_count: 128,
    detail_url: null,
    ...over,
  };
}

function page(items: DrillPromotionRow[], total = items.length, p = 1): DrillPromotionPage {
  return { items, total, page: p, page_size: DRILL_PAGE_SIZE };
}

function renderModal(target: DrillPromotionTarget | null) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrap = (node: ReactNode) => (
    <QueryClientProvider client={client}>{node}</QueryClientProvider>
  );
  return render(wrap(<DrillPromotionModal target={target} onClose={() => {}} />));
}

const TARGET: DrillPromotionTarget = {
  prId: PR1,
  prName: "小王",
  dateFrom: "2026-10-01",
  dateTo: "2026-10-11",
};

beforeEach(() => {
  vi.mocked(getDrillPromotions).mockReset();
  vi.mocked(getDrillPromotions).mockResolvedValue(page([row({})]));
});

describe("drillPromotionParams", () => {
  it("PR 行传该 PR 的 id；未分配行传 unassigned（不能不传，不传 = 所有人的单）", () => {
    expect(drillPromotionParams(TARGET, 1)).toEqual({
      date_from: "2026-10-01",
      date_to: "2026-10-11",
      pr_id: PR1,
      page: 1,
      page_size: 20,
    });
    expect(drillPromotionParams({ ...TARGET, prId: null }, 3).pr_id).toBe("unassigned");
  });
});

describe("DrillPromotionModal", () => {
  it("关闭时不请求", () => {
    renderModal(null);
    expect(getDrillPromotions).not.toHaveBeenCalled();
  });

  it("打开即按 PR + 区间请求第一页，标题与区间可见", async () => {
    renderModal(TARGET);
    await screen.findByText("TG-001");
    expect(getDrillPromotions).toHaveBeenCalledWith({
      date_from: "2026-10-01",
      date_to: "2026-10-11",
      pr_id: PR1,
      page: 1,
      page_size: 20,
    });
    expect(screen.getByText("小王 的推广单")).toBeTruthy();
    expect(screen.getByText("合作日期 2026-10-01 至 2026-10-11")).toBeTruthy();
  });

  it("未分配行请求 pr_id=unassigned", async () => {
    renderModal({ ...TARGET, prId: null, prName: "未分配" });
    await screen.findByText("TG-001");
    expect(vi.mocked(getDrillPromotions).mock.calls[0][0].pr_id).toBe("unassigned");
    expect(screen.getByText("未分配 的推广单")).toBeTruthy();
  });

  it("金额读不到（null）显示「—」，读得到显示金额；未录点赞也是「—」", async () => {
    vi.mocked(getDrillPromotions).mockResolvedValue(
      page([
        row({}),
        row({
          id: "p2",
          internal_code: "TG-002",
          quote_amount: null,
          total_promo_cost: null,
          like_count: null,
        }),
      ])
    );
    renderModal(TARGET);
    const visible = (await screen.findByText("TG-001")).closest("tr") as HTMLElement;
    expect(within(visible).getByText("¥300.00")).toBeTruthy();
    expect(within(visible).getByText("¥420.50")).toBeTruthy();
    expect(within(visible).getByText("128")).toBeTruthy();
    const hidden = screen.getByText("TG-002").closest("tr") as HTMLElement;
    // 发布日期、服务费、站外推广成本、7天点赞 → 4 个「—」
    expect(within(hidden).getAllByText("—")).toHaveLength(4);
    expect(within(hidden).queryByText(/¥/)).toBeNull();
  });

  it("不渲染任何链接（detail_url 有值也不渲染，R8 才接详情）", async () => {
    vi.mocked(getDrillPromotions).mockResolvedValue(
      page([row({ detail_url: "/promotions?id=p1" })])
    );
    renderModal(TARGET);
    await screen.findByText("TG-001");
    const dialog = screen.getByRole("dialog");
    expect(dialog.querySelector("a[href]")).toBeNull();
    expect(within(dialog).queryByText("/promotions?id=p1")).toBeNull();
  });

  it("翻页请求下一页，每页 20 条", async () => {
    vi.mocked(getDrillPromotions).mockResolvedValue(page([row({})], 45));
    renderModal(TARGET);
    await screen.findByText("共 45 条");
    fireEvent.click(screen.getByTitle("2"));
    await waitFor(() =>
      expect(getDrillPromotions).toHaveBeenLastCalledWith(
        expect.objectContaining({ page: 2, page_size: 20, pr_id: PR1 })
      )
    );
  });
});