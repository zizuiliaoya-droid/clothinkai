// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BloggerWorks } from "./BloggerWorks";
import { listWorks, saveWork } from "@/features/blogger/worksApi";
import type { WorkHitSummary, WorkPage } from "@/features/blogger/worksApi";

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
  is_hit: false,
};
const summary: WorkHitSummary = {
  sample_count: 1, valid_like_count: 1, published_from: "2026-11-01", published_to: "2026-11-01",
  median_likes: null, threshold: null, status: "insufficient_samples",
};
const data: WorkPage = { items: [work], total: 1, page: 1, page_size: 10, can_write: true, can_read_source: false, hit_summary: summary };

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
    expect(screen.getByText(/有效点赞不足 10 篇，仅展示数据/)).toBeInTheDocument();
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

  it("使用后端的跨页样本和阈值，只标记样本内爆款", async () => {
    const ready: WorkHitSummary = { ...summary, sample_count: 30, valid_like_count: 29,
      published_from: "2026-10-03", median_likes: 10, threshold: 30, status: "ready" };
    vi.mocked(listWorks).mockImplementation(async (_, filters) => ({
      ...data, total: 32, page: filters?.page ?? 1, hit_summary: ready,
      items: [{ ...work, like_count: 1000, is_hit: filters?.page !== 4 }],
    }));
    mount(); fireEvent.click(screen.getByText("作品与采集记录"));
    expect(await screen.findByText("爆款")).toBeInTheDocument();
    expect(screen.getByText(/最近的 30 篇作品.*有效点赞 29 篇/)).toHaveTextContent("2026-10-03 至 2026-11-01");
    expect(screen.getByText(/点赞中位数 10，爆款阈值 30/)).toHaveTextContent("仅对上述样本");
    fireEvent.click(screen.getByTitle("4"));
    await waitFor(() => expect(listWorks).toHaveBeenLastCalledWith("blogger-1", { page: 4, date_from: undefined, date_to: undefined }));
    await waitFor(() => expect(screen.queryByText("爆款")).toBeNull());
    expect(screen.getByText(/点赞中位数 10，爆款阈值 30/)).toBeInTheDocument();
  });

  it("中位数为零显示实际阈值与暂不判定提示", async () => {
    vi.mocked(listWorks).mockResolvedValue({ ...data, hit_summary: {
      ...summary, sample_count: 10, valid_like_count: 10, median_likes: 0, threshold: 0, status: "zero_median",
    } });
    mount(); fireEvent.click(screen.getByText("作品与采集记录"));
    expect(await screen.findByText(/点赞中位数 0，爆款阈值 0/)).toHaveTextContent("中位数为 0，暂不判定爆款");
    expect(screen.queryByText("爆款")).toBeNull();
  });
});
