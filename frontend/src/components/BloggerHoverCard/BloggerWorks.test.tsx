// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BloggerWorks } from "./BloggerWorks";
import { listWorks, saveWork } from "@/features/blogger/worksApi";

const state = vi.hoisted(() => ({ readOnly: false }));
vi.mock("@/stores/supportStore", () => ({ useReadOnlySupport: () => state.readOnly }));
vi.mock("@/features/blogger/api", () => ({ getBlogger: vi.fn(async () => ({ platform: "抖音", xiaohongshu_id: "acct", follower_count: null, homepage_url: "https://example.com/home" })) }));
vi.mock("@/features/blogger/worksApi", () => ({ listWorks: vi.fn(), saveWork: vi.fn(), getWorkSource: vi.fn() }));
vi.mock("@/components/ImportUploadButton", () => ({ ImportUploadButton: ({ source }: { source: string }) => <button>{source}</button> }));

const work = {
  id: "snapshot-1", work_id: "work-1", title: "作品一", published_on: "2026-11-01",
  collected_at: "2026-11-05T06:30:00Z", source: "平台采集", like_count: 0,
  comment_count: null, share_count: null, collect_count: null, play_count: null,
  interaction_total: null, collect_like_ratio: null, share_like_ratio: null, comment_like_ratio: null,
  platform: "抖音", created_by_name: "小李", import_batch_id: null, row_number: null,
};
const data = { items: [work], total: 1, page: 1, page_size: 10, can_write: true, can_read_source: false };

beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => getStyle(element));
  Object.defineProperty(window, "matchMedia", { writable: true, value: (query: string) => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false }) });
});
beforeEach(() => { state.readOnly = false; vi.mocked(listWorks).mockReset().mockResolvedValue(data); vi.mocked(saveWork).mockReset().mockResolvedValue({ id: "new", inserted: true }); });
afterEach(cleanup);

function mount(enabled = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><BloggerWorks bloggerId="blogger-1" enabled={enabled} /></QueryClientProvider>);
}

describe("作品卡片", () => {
  it("展开前不加载作品，展开后保留空值并能按日期筛选、查看记录", async () => {
    mount();
    expect(listWorks).not.toHaveBeenCalled();
    expect(await screen.findByText("打开主页")).toHaveAttribute("href", "https://example.com/home");
    fireEvent.click(screen.getByText("作品与采集记录"));
    expect(await screen.findByText("作品一")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(4);
    expect(screen.queryByText("爆款")).toBeNull();
    fireEvent.change(screen.getByLabelText("发布起日"), { target: { value: "2026-11-01" } });
    await waitFor(() => expect(listWorks).toHaveBeenLastCalledWith("blogger-1", { page: 1, date_from: "2026-11-01", date_to: undefined }));
    fireEvent.click(await screen.findByRole("button", { name: /expand row|展开行/i }));
    await waitFor(() => expect(listWorks).toHaveBeenCalledWith("blogger-1", { snapshot_id: "snapshot-1", page: 1 }));
    expect(await screen.findByText("手工补录")).toBeInTheDocument();
  });

  it("手工补录提交原始计数和明确采集时间", async () => {
    mount(); fireEvent.click(screen.getByText("作品与采集记录"));
    fireEvent.click(await screen.findByText("补录作品"));
    fireEvent.change(screen.getByLabelText("作品 ID"), { target: { value: "123" } });
    fireEvent.change(screen.getByLabelText("发布日期"), { target: { value: "2026-11-01" } });
    fireEvent.change(screen.getByLabelText("采集时间（北京时间）"), { target: { value: "2026-11-05T14:30" } });
    fireEvent.change(screen.getByLabelText("采集来源"), { target: { value: "人工录入" } });
    fireEvent.change(screen.getByLabelText("点赞"), { target: { value: "14.3w" } });
    fireEvent.click(screen.getByRole("button", { name: /OK|确定|确 定/ }));
    await waitFor(() => expect(saveWork).toHaveBeenCalledWith("blogger-1", expect.objectContaining({ work_id: "123", source: "人工录入", like_count: "14.3w" })));
    expect(vi.mocked(saveWork).mock.calls[0][1].share_count).toBeUndefined();
  });

  it("支持会话不显示补录或导入", async () => {
    state.readOnly = true;
    mount(); fireEvent.click(screen.getByText("作品与采集记录"));
    await screen.findByText("作品一");
    expect(screen.queryByText("补录作品")).toBeNull();
    expect(screen.queryByText("manual_blogger_work")).toBeNull();
  });
});
