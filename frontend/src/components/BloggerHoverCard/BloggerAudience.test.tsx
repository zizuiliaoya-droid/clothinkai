// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BloggerAudience } from "./BloggerAudience";
import { listAudience, saveAudience } from "@/features/blogger/audienceApi";
import type { AudiencePage } from "@/features/blogger/audienceApi";

const state = vi.hoisted(() => ({ readOnly: false }));
vi.mock("@/stores/supportStore", () => ({ useReadOnlySupport: () => state.readOnly }));
vi.mock("@/features/blogger/audienceApi", async (original) => ({ ...await original<typeof import("@/features/blogger/audienceApi")>(), listAudience: vi.fn(), saveAudience: vi.fn(), getAudienceSource: vi.fn() }));
vi.mock("@/components/ImportUploadButton", () => ({ ImportUploadButton: ({ source }: { source: string }) => <button>{source}</button> }));

const data: AudiencePage = { items: [{ id: "capture-1", dimension: "age", collected_on: "2026-11-05", source: "截图Top5", completeness: "partial", estimated: true, is_current: true,
  entries: [{ label: "41–50", percentage: 23.5, tgi: null }, { label: "50+", percentage: 0, tgi: 120 }, { label: "未知", percentage: null, tgi: 110 }],
  tgi_source: "平台整体用户基准", import_batch_id: null, row_numbers: [], created_by_name: "小李" }], total: 1, page: 1, page_size: 10, can_write: true, can_read_source: false };

beforeAll(() => {
  const getStyle = window.getComputedStyle.bind(window);
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => getStyle(element));
  Object.defineProperty(window, "matchMedia", { writable: true, value: (query: string) => ({ matches: false, media: query, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false }) });
});
beforeEach(() => { state.readOnly = false; vi.mocked(listAudience).mockReset().mockResolvedValue(data); vi.mocked(saveAudience).mockReset().mockResolvedValue({ id: "new", inserted: true, current: false, message: "采集日期较旧，已归档，当前画像保持不变" }); });
afterEach(cleanup);

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><BloggerAudience bloggerId="blogger-1" enabled /></QueryClientProvider>);
}

describe("画像卡片", () => {
  it("按需加载、原年龄标签与实际占比、空值和历史范围", async () => {
    mount(); expect(listAudience).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("受众画像与采集记录"));
    await screen.findByText("截图Top5");
    fireEvent.click(screen.getByRole("button", { name: /expand row|展开行/i }));
    expect(await screen.findByText("部分数据")).toBeInTheDocument();
    expect(screen.getByText(/未补齐或归一化/)).toBeInTheDocument();
    expect(screen.getAllByText("41–50").length).toBeGreaterThan(0);
    expect(screen.getAllByText("0%").length).toBeGreaterThan(0);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByText(/平台整体用户基准/)).toBeInTheDocument();
    fireEvent.click(screen.getByText("查看采集历史"));
    await waitFor(() => expect(listAudience).toHaveBeenLastCalledWith("blogger-1", true, 1));
  });

  it("手工按维度提交、百分数文本不猜换算并显示归档提示", async () => {
    mount(); fireEvent.click(screen.getByText("受众画像与采集记录"));
    fireEvent.click(await screen.findByText("补录画像"));
    fireEvent.change(screen.getByLabelText("采集日期"), { target: { value: "2026-10-01" } });
    fireEvent.change(screen.getByLabelText("采集来源"), { target: { value: "截图" } });
    fireEvent.change(screen.getByLabelText("原始分类"), { target: { value: "41–50" } });
    fireEvent.change(screen.getByLabelText("占比（%）"), { target: { value: "23.5%" } });
    fireEvent.click(screen.getByRole("button", { name: /OK|确定|确 定/ }));
    await waitFor(() => expect(saveAudience).toHaveBeenCalledWith("blogger-1", expect.objectContaining({ dimension: "age", completeness: "partial", entries: [{ label: "41–50", percentage: "23.5%" }] })));
    expect(await screen.findByText("采集日期较旧，已归档，当前画像保持不变")).toBeInTheDocument();
  });

  it("support只读隐藏导入、补录和原文件", async () => {
    state.readOnly = true; mount(); fireEvent.click(screen.getByText("受众画像与采集记录"));
    await screen.findByText("截图Top5");
    expect(screen.getByText(/当前为只读/)).toBeInTheDocument();
    expect(screen.queryByText("补录画像")).toBeNull();
    expect(screen.queryByText("manual_blogger_audience")).toBeNull();
    expect(screen.queryByText("原文件")).toBeNull();
  });
});
