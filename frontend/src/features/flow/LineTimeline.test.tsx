// @vitest-environment jsdom
// 时间线渲染：下一步在最上、上线前记录整段标注、被隐藏的谈款段一行、「改了什么」展开、页面文字不出商品编码；
// 抽屉的留言框：ui.actions.comment 可点才出现、空留言拦住、发文字带上 lineId。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { LineDrawer } from "./LineDrawer";
import { LineTimeline } from "./LineTimeline";
import { COMMENT_EMPTY_TEXT, COMMENT_RECEIVER_HINT, LEGACY_LABEL, type LineTimelineData } from "./timeline";

const api = vi.hoisted(() => ({
  getLineEvents: vi.fn(),
  postLineComment: vi.fn(),
}));
vi.mock("./api", () => ({
  lineEventsKey: (id: string | null) => ["line-events", id],
  getLineEvents: api.getLineEvents,
  postLineComment: api.postLineComment,
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
beforeEach(() => {
  api.getLineEvents.mockReset();
  api.postLineComment.mockReset();
});

const CODE = "SUIT-1074568657697";

function data(over: Partial<LineTimelineData> = {}): LineTimelineData {
  return {
    line_id: "L1",
    negotiation: null,
    promotion: { id: "p1", internal_code: "TG-0001", stage: "待仓库发货" },
    next_step: { text: "等待仓库打单发货", who: "仓库" },
    events: [
      {
        seq: 3,
        event_type: "ship.pushed",
        group: "发货",
        created_at: "2026-10-12T02:00:00Z",
        actor: { id: "u1", display_name: "PR 甲" },
        from_state: "待发货",
        to_state: "待打单",
        note: null,
        attachment: null,
        payload: {
          items: [["上衣", "黑色", "M"]],
          diff: { receiver_name: { before: "李四", after: "张三" }, style_id: { before: CODE, after: CODE } },
          goods_main_id: CODE,
        },
      },
      {
        seq: 2,
        event_type: "line.comment",
        group: "留言",
        created_at: "2026-10-11T02:00:00Z",
        actor: null,
        from_state: null,
        to_state: null,
        note: "博主说周五发",
        attachment: { id: "a1", url: "https://example.test/a1.png", mime: "image/png" },
        payload: {},
      },
    ],
    hidden_segments: [{ group: "谈款", count: 6, reason: "谈款过程只给谈款人和能看全部谈款的人看" }],
    legacy: [{ at: "2026-09-01T00:00:00Z", text: "新建推广单", source: "promotion.created_at" }],
    truncated: false,
    ui: { actions: {} },
    ...over,
  };
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{node}</QueryClientProvider>;
}

describe("LineTimeline", () => {
  it("下一步在最上，事件按给的顺序（最新在上）", () => {
    const { container } = render(<LineTimeline data={data()} />);
    const text = container.textContent ?? "";
    expect(text.indexOf("下一步：等待仓库打单发货")).toBeGreaterThanOrEqual(0);
    expect(text.indexOf("下一步")).toBeLessThan(text.indexOf("推送仓库发货"));
    expect(text.indexOf("推送仓库发货")).toBeLessThan(text.indexOf("博主说周五发"));
    expect(screen.getByText("仓库")).toBeTruthy();
  });

  it("上线前记录整段标注，点在标注下面", () => {
    render(<LineTimeline data={data()} />);
    const section = screen.getByRole("region", { name: LEGACY_LABEL });
    expect(section.textContent).toContain(LEGACY_LABEL);
    expect(section.textContent).toContain("新建推广单");
  });

  it("只有上线前记录的旧单也出标注；什么都没有显示「还没有记录」", () => {
    render(<LineTimeline data={data({ events: [], hidden_segments: [], next_step: null })} />);
    expect(screen.getByText(LEGACY_LABEL)).toBeTruthy();
    cleanup();
    render(<LineTimeline data={data({ events: [], hidden_segments: [], next_step: null, legacy: [] })} />);
    expect(screen.getByText("还没有记录")).toBeTruthy();
  });

  it("被隐藏的谈款段折成一行说明", () => {
    render(<LineTimeline data={data()} />);
    expect(screen.getByText("已隐藏 6 条谈款记录：谈款过程只给谈款人和能看全部谈款的人看")).toBeTruthy();
  });

  it("「改了什么」可展开，明细显示简称颜色尺码；页面文字没有商品编码", () => {
    const { container } = render(<LineTimeline data={data()} />);
    const summary = screen.getByText("改了什么");
    expect(summary.tagName).toBe("SUMMARY");
    const details = summary.parentElement as HTMLDetailsElement;
    expect(details.open).toBe(false);
    fireEvent.click(summary);
    expect(details.textContent).toContain("收件人：李四 → 张三");
    expect(details.textContent).toContain("主款：已更换");
    expect(details.textContent).toContain("发货明细：上衣 黑色 / M");
    expect(container.textContent).not.toContain(CODE);
    expect(container.textContent).not.toContain("ship.pushed");
  });

  it("系统动作显示「系统」，贴图有 alt", () => {
    render(<LineTimeline data={data()} />);
    expect(screen.getByText(/· 系统$/)).toBeTruthy();
    expect(screen.getByAltText("留言贴图")).toBeTruthy();
  });

  it("超过 500 条提示只显示最近的", () => {
    render(<LineTimeline data={data({ truncated: true })} />);
    expect(screen.getByText("记录太多，只显示最近 500 条")).toBeTruthy();
  });
});

describe("LineDrawer 留言框", () => {
  it("ui.actions.comment 不可点 → 没有留言框", async () => {
    api.getLineEvents.mockResolvedValue(data({ ui: { actions: { comment: { state: "disabled", reason: "只读" } } } }));
    render(wrap(<LineDrawer lineId="L1" onClose={vi.fn()} goodsName="上衣" bloggerName="小红" />));
    await screen.findByText("推送仓库发货");
    expect(api.getLineEvents).toHaveBeenCalledWith("L1");
    expect(screen.getByText("TG-0001")).toBeTruthy();
    expect(screen.getByText("上衣")).toBeTruthy();
    expect(screen.queryByLabelText("留言")).toBeNull();
    expect(screen.queryByText(COMMENT_RECEIVER_HINT)).toBeNull();
  });

  it("可点 → 出留言框与收货提示；空留言拦住不发，写了字才发", async () => {
    api.getLineEvents.mockResolvedValue(data({ ui: { actions: { comment: { state: "enabled" } } } }));
    api.postLineComment.mockResolvedValue({});
    render(wrap(<LineDrawer lineId="L1" onClose={vi.fn()} />));
    const box = await screen.findByLabelText("留言");
    expect(screen.getByText(COMMENT_RECEIVER_HINT)).toBeTruthy();

    fireEvent.change(box, { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "发 送" }));
    expect(await screen.findByText(COMMENT_EMPTY_TEXT)).toBeTruthy();
    expect(api.postLineComment).not.toHaveBeenCalled();

    fireEvent.change(box, { target: { value: "博主说周五发" } });
    fireEvent.click(screen.getByRole("button", { name: "发 送" }));
    await waitFor(() => expect(api.postLineComment).toHaveBeenCalledTimes(1));
    expect(api.postLineComment.mock.calls[0][0]).toBe("L1");
    expect(api.postLineComment.mock.calls[0][1]).toMatchObject({ text: "博主说周五发", image: null });
    await waitFor(() => expect(api.getLineEvents).toHaveBeenCalledTimes(2));
  });

  it("看不到这条线（404）→ 提示文字，不出时间线", async () => {
    api.getLineEvents.mockRejectedValue({ response: { status: 404, data: { code: "LINE_NOT_FOUND" } } });
    render(wrap(<LineDrawer lineId="L1" onClose={vi.fn()} />));
    expect(await screen.findByText("看不到这条记录（没有权限，或单据已删除）")).toBeTruthy();
  });
});
