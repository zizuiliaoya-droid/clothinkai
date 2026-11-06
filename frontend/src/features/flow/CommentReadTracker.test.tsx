// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { LineTimelineData, TimelineEvent } from "./timeline";

const mark = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ markLineCommentsRead: mark, lineEventsKey: (id: string) => ["line-events", id] }));
import { CommentReadTracker } from "./CommentReadTracker";

const event = (seq: number, event_type = "line.comment") => ({ seq, event_type } as TimelineEvent);
const data = (extra: Partial<LineTimelineData> = {}) => ({ line_id: "L1", events: [event(99, "ship.filled"), event(7), event(3)],
  comment_state: { last_comment_at: null, last_read_comment_seq: 0, unread_comment_count: 2 }, ...extra } as LineTimelineData);
function mount(value: LineTimelineData) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["line-events", "L1"], value);
  return { client, ...render(<QueryClientProvider client={client}><CommentReadTracker lineId="L1" data={value} /></QueryClientProvider>) };
}
beforeEach(() => { mark.mockReset(); mark.mockResolvedValue({ last_comment_at: null, last_read_comment_seq: 7, unread_comment_count: 0 }); });
afterEach(cleanup);

describe("展示后的阅读确认", () => {
  it("只提交本次展示的最大留言seq，忽略后续业务事件seq", async () => {
    const { client } = mount(data());
    await waitFor(() => expect(mark).toHaveBeenCalledWith("L1", 7));
    await waitFor(() => expect(client.getQueryData<LineTimelineData>(["line-events", "L1"])?.comment_state?.last_read_comment_seq).toBe(7));
  });
  it("没有留言读取权限或已读完时不提交", () => {
    const first = mount(data({ comment_state: null }));
    expect(mark).not.toHaveBeenCalled(); first.unmount();
    mount(data({ comment_state: { last_comment_at: null, last_read_comment_seq: 7, unread_comment_count: 0 } }));
    expect(mark).not.toHaveBeenCalled();
  });
  it("阅读确认失败保留重试入口，不自动反复请求", async () => {
    mark.mockRejectedValueOnce(new Error("offline"));
    mount(data());
    fireEvent.click(await screen.findByRole("button", { name: "重试" }));
    await waitFor(() => expect(mark).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole("button", { name: "重试" })).toBeNull());
  });
  it("关闭后的迟到结果不会覆盖当前时间线", async () => {
    let finish!: (state: unknown) => void;
    mark.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    const { client, unmount } = mount(data());
    unmount();
    finish({ last_comment_at: null, last_read_comment_seq: 7, unread_comment_count: 0 });
    await Promise.resolve();
    expect(client.getQueryData<LineTimelineData>(["line-events", "L1"])?.comment_state?.last_read_comment_seq).toBe(0);
  });
});
