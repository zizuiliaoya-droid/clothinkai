// @vitest-environment jsdom
// 催发页（流程线 8.7）：标记完成要截图、「只看我的」传 pr_id 并记住、已完成的催发先提示重开、抽屉显示完成截图。
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { UrgeTask, UrgeTaskDetail, UrgeTaskPage } from "./types";

const listMock = vi.fn();
const detailMock = vi.fn();
const completeMock = vi.fn();
vi.mock("@/features/urge/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/features/urge/api")>();
  return {
    ...actual,
    listUrgeTasks: (...args: unknown[]) => listMock(...args),
    getUrgeTask: (...args: unknown[]) => detailMock(...args),
    completeUrgeTask: (...args: unknown[]) => completeMock(...args),
    getUrgeDashboard: vi.fn().mockResolvedValue(null),
    getUrgeConfig: vi.fn(),
    updateUrgeConfig: vi.fn(),
    closeUrgeTask: vi.fn(),
    urgeBatch: vi.fn(),
    urgePromotion: vi.fn(),
    urgePromotionWithScreenshot: vi.fn(),
  };
});

const getPrefMock = vi.fn();
const savePrefMock = vi.fn();
vi.mock("@/features/preference/api", () => ({
  getFilterPreference: (...args: unknown[]) => getPrefMock(...args),
  saveFilterPreference: (...args: unknown[]) => savePrefMock(...args),
}));

vi.mock("@/features/product/api", () => ({
  listStyles: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20 }),
}));

import { UrgeCompleteModal } from "./UrgeCompleteModal";
import { UrgePage } from "@/pages/UrgePage";
import { useAuthStore } from "@/stores/authStore";

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

function task(over: Partial<UrgeTask> = {}): UrgeTask {
  return {
    id: "t-1",
    promotion_id: "p-1",
    promotion_internal_code: "TG-001",
    blogger_id: "b-1",
    blogger_nickname: "小美",
    pr_id: "u-pr",
    pr_name: "王小美",
    style_code: "ST-001",
    style_name: "飞狐短裙",
    display_short_name: "飞狐短裙",
    goods_title: null,
    scheduled_publish_date: "2026-10-20",
    publish_status: "未发布",
    status: "进行中",
    urge_count: 1,
    last_urged_at: null,
    closed_at: null,
    close_reason: null,
    over_limit: false,
    overdue_days: null,
    created_at: "2026-10-10T00:00:00Z",
    updated_at: "2026-10-10T00:00:00Z",
    ...over,
  };
}

function page(items: UrgeTask[]): UrgeTaskPage {
  return { items, total: items.length, page: 1, page_size: 20 };
}

beforeEach(() => {
  listMock.mockReset();
  detailMock.mockReset();
  completeMock.mockReset();
  getPrefMock.mockReset();
  savePrefMock.mockReset();
  getPrefMock.mockResolvedValue({});
  savePrefMock.mockResolvedValue(undefined);
  listMock.mockResolvedValue(page([task()]));
  useAuthStore.setState({
    user: {
      id: "u-pr",
      username: "pr",
      display_name: null,
      email: null,
      status: "active",
      roles: ["pr"],
    },
    isAuthenticated: true,
  });
});
afterEach(cleanup);

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function pickFile(dialog: HTMLElement, file: File) {
  const input = dialog.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

describe("UrgeCompleteModal", () => {
  it("没选截图「标记完成」是灰的，选了才能提交并带上文件", async () => {
    completeMock.mockResolvedValue({ ...task(), status: "已关闭", close_reason: "已完成", records: [] });
    const onClose = vi.fn();
    wrap(<UrgeCompleteModal task={task()} onClose={onClose} />);
    const dialog = await screen.findByRole("dialog");
    const ok = within(dialog).getByRole("button", { name: /标记完成/ });
    expect((ok as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(ok);
    expect(completeMock).not.toHaveBeenCalled();

    const file = new File(["png"], "done.png", { type: "image/png" });
    pickFile(dialog, file);
    await waitFor(() => expect((ok as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(ok);
    await waitFor(() => expect(completeMock).toHaveBeenCalledTimes(1));
    expect(completeMock.mock.calls[0][0]).toBe("t-1");
    expect(completeMock.mock.calls[0][1]).toBe(file);
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("格式不对的图不收，按钮仍是灰的", async () => {
    wrap(<UrgeCompleteModal task={task()} onClose={vi.fn()} />);
    const dialog = await screen.findByRole("dialog");
    pickFile(dialog, new File(["x"], "a.gif", { type: "image/gif" }));
    const ok = within(dialog).getByRole("button", { name: /标记完成/ });
    expect((ok as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("UrgePage", () => {
  it("打开「只看我的」后列表带 pr_id = 当前用户，并记进筛选偏好", async () => {
    wrap(<UrgePage />);
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(listMock.mock.calls[0][0].pr_id).toBeUndefined();
    expect(getPrefMock).toHaveBeenCalledWith("urge_tasks");

    fireEvent.click(screen.getByRole("switch"));
    await waitFor(() =>
      expect(listMock.mock.calls.at(-1)?.[0]).toMatchObject({ pr_id: "u-pr", page: 1 })
    );
    await waitFor(
      () =>
        expect(savePrefMock).toHaveBeenCalledWith(
          "urge_tasks",
          expect.objectContaining({ mine: true })
        ),
      { timeout: 2000 }
    );
  });

  it("记住的「只看我的」和款式在第一次请求就生效，读回来之前不发请求", async () => {
    let release: (v: unknown) => void = () => {};
    getPrefMock.mockReturnValue(new Promise((r) => (release = r)));
    wrap(<UrgePage />);
    await new Promise((r) => setTimeout(r, 50));
    expect(listMock).not.toHaveBeenCalled();
    release({ mine: true, style_id: "s-9", style_label: "ST-009 法式长裙" });
    await waitFor(() => expect(listMock).toHaveBeenCalled());
    expect(listMock.mock.calls[0][0]).toMatchObject({ pr_id: "u-pr", style_id: "s-9" });
    expect(await screen.findByText("ST-009 法式长裙")).toBeTruthy();
  });

  it("已完成的任务能催发但先提示会重开；其他原因关闭的不能催、都不能再标记完成", async () => {
    listMock.mockResolvedValue(
      page([
        task({ id: "t-done", status: "已关闭", close_reason: "已完成" }),
        task({ id: "t-pub", status: "已关闭", close_reason: "博主已发布" }),
      ])
    );
    wrap(<UrgePage />);
    const urgeBtns = await screen.findAllByRole("button", { name: "催发" });
    expect((urgeBtns[0] as HTMLButtonElement).disabled).toBe(false);
    expect((urgeBtns[1] as HTMLButtonElement).disabled).toBe(true);
    for (const b of screen.getAllByRole("button", { name: "标记完成" })) {
      expect((b as HTMLButtonElement).disabled).toBe(true);
    }
    fireEvent.click(urgeBtns[0]);
    expect(await screen.findByText("这单已标记完成，催发后会重新打开任务")).toBeTruthy();
  });

  it("进行中的任务点「标记完成」打开截图弹窗；抽屉里已完成的显示完成截图", async () => {
    const detail: UrgeTaskDetail = {
      ...task({ id: "t-done", status: "已关闭", close_reason: "已完成" }),
      records: [],
      close_screenshot_url: "https://example.test/done.png",
    };
    listMock.mockResolvedValue(page([task(), detail]));
    detailMock.mockResolvedValue(detail);
    wrap(<UrgePage />);
    const completeBtns = await screen.findAllByRole("button", { name: "标记完成" });
    fireEvent.click(completeBtns[0]);
    expect(await screen.findByText(/标记完成 · TG-001/)).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "时间线" })[1]);
    const img = await screen.findByAltText("完成截图");
    expect(img.getAttribute("src")).toBe("https://example.test/done.png");
  });
});
