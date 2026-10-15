// @vitest-environment jsdom
// 系统设置 ·「平台指标」（7e R7）：can_edit 才可编辑；保存先确认，确认才 PUT、取消不发；409 提示稍后再试；
// 422 按 loc 行号标到单元格；无改动保存是灰的；清空的格子先标「必填」；页面文字里没有商品编码。
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Modal } from "antd";
import type { ReactNode } from "react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { PromotionPlatformSettings } from "@/features/settings/api";

const getMock = vi.fn();
const updateMock = vi.fn();
const scrollMock = vi.fn();
vi.mock("@/features/settings/api", () => ({
  getPromotionPlatformSettings: (...args: unknown[]) => getMock(...args),
  updatePromotionPlatformSettings: (...args: unknown[]) => updateMock(...args),
}));

import {
  BUSY_TEXT,
  CONFIRM_TEXT,
  PromotionPlatformSettingTab,
  READONLY_TEXT,
} from "./PromotionPlatformSettingTab";

beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
    configurable: true,
    value: scrollMock,
  });
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

const ITEMS = [
  { platform: "小红书", like_coefficient: "1.000", hit_threshold: 1000 },
  { platform: "抖音", like_coefficient: "0.100", hit_threshold: 10000 },
  { platform: "快手", like_coefficient: "0.100", hit_threshold: 1000 },
  { platform: "B站", like_coefficient: "1.000", hit_threshold: 1000 },
  { platform: "得物", like_coefficient: "1.000", hit_threshold: 1000 },
];

function settings(over: Partial<PromotionPlatformSettings> = {}): PromotionPlatformSettings {
  return {
    items: ITEMS.map((r) => ({ ...r })),
    can_edit: true,
    changed: [],
    quality_recompute_queued: false,
    ...over,
  };
}

function wrap(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

function saveButton() {
  return screen.getByRole("button", { name: "保存修改" }) as HTMLButtonElement;
}

function input(label: string) {
  return screen.getByLabelText(label) as HTMLInputElement;
}

async function openConfirm() {
  fireEvent.click(saveButton());
  return screen.findByText(CONFIRM_TEXT);
}

beforeEach(() => {
  getMock.mockReset();
  updateMock.mockReset();
  scrollMock.mockClear();
  getMock.mockResolvedValue(settings());
});

afterEach(() => {
  Modal.destroyAll();
  cleanup();
});

describe("PromotionPlatformSettingTab", () => {
  it("可编辑：5 行 10 个输入框；没改动保存是灰的，改了才亮", async () => {
    wrap(<PromotionPlatformSettingTab />);
    expect(await screen.findByLabelText("抖音点赞折算系数")).toBeTruthy();
    expect(screen.getAllByRole("spinbutton")).toHaveLength(10);
    expect(input("抖音爆文阈值").value).toBe("10000");
    expect(saveButton().disabled).toBe(true);
    fireEvent.change(input("抖音点赞折算系数"), { target: { value: "0.5" } });
    expect(saveButton().disabled).toBe(false);
    // 改回原值（数值相等）又是灰的
    fireEvent.change(input("抖音点赞折算系数"), { target: { value: "0.1" } });
    expect(saveButton().disabled).toBe(true);
  });

  it("保存先弹确认：取消不发请求，确认才 PUT 全量 5 行", async () => {
    updateMock.mockResolvedValue(
      settings({
        items: ITEMS.map((r) => (r.platform === "抖音" ? { ...r, like_coefficient: "0.500" } : r)),
        changed: ["抖音"],
        quality_recompute_queued: true,
      })
    );
    wrap(<PromotionPlatformSettingTab />);
    fireEvent.change(await screen.findByLabelText("抖音点赞折算系数"), {
      target: { value: "0.5" },
    });
    await openConfirm();
    fireEvent.click(screen.getByRole("button", { name: /取\s*消/ }));
    await waitFor(() => expect(screen.queryByText(CONFIRM_TEXT)).toBeNull());
    expect(updateMock).not.toHaveBeenCalled();

    await openConfirm();
    expect(updateMock).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认保存" }));
    await waitFor(() => expect(updateMock).toHaveBeenCalledTimes(1));
    expect(updateMock.mock.calls[0][0]).toEqual(
      ITEMS.map((r) => (r.platform === "抖音" ? { ...r, like_coefficient: "0.5" } : r))
    );
    expect(await screen.findByText("已保存，博主标签将在几分钟内更新")).toBeTruthy();
    // 保存后以服务端为准，又回到无改动
    await waitFor(() => expect(saveButton().disabled).toBe(true));
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("heading", { name: "平台指标" }))
    );
  });

  it("只读（can_edit 假）：没有输入框和保存按钮，显示值与提示", async () => {
    getMock.mockResolvedValue(settings({ can_edit: false }));
    wrap(<PromotionPlatformSettingTab />);
    expect(await screen.findByText(READONLY_TEXT)).toBeTruthy();
    expect(screen.queryAllByRole("spinbutton")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "保存修改" })).toBeNull();
    expect(screen.getByText("10,000")).toBeTruthy();
    expect(screen.getAllByText("×0.1")).toHaveLength(2);
  });

  it("撞刷新锁 409：提示报表正在刷新，改动还在", async () => {
    updateMock.mockRejectedValue({
      response: {
        status: 409,
        data: { code: "REPORT_SUMMARY_REFRESH_BUSY", message: "报表正在刷新，请稍后再保存" },
      },
    });
    wrap(<PromotionPlatformSettingTab />);
    fireEvent.change(await screen.findByLabelText("小红书爆文阈值"), {
      target: { value: "4090" },
    });
    await openConfirm();
    fireEvent.click(screen.getByRole("button", { name: "确认保存" }));
    expect(await screen.findByText(BUSY_TEXT)).toBeTruthy();
    expect(input("小红书爆文阈值").value).toBe("4090");
    expect(saveButton().disabled).toBe(false);
  });

  it("422：按 loc 行号把错误标到那一格，改了那格错误就消失", async () => {
    updateMock.mockRejectedValue({
      response: {
        status: 422,
        data: {
          code: "VALIDATION_ERROR",
          message: "请求数据校验失败",
          details: {
            errors: [
              { loc: ["body", 4, "hit_threshold"], msg: "Input should be less than or equal to 10000000" },
              { loc: ["body", 2, "like_coefficient"], msg: "Decimal input should have no more than 3 decimal places" },
            ],
          },
        },
      },
    });
    wrap(<PromotionPlatformSettingTab />);
    fireEvent.change(await screen.findByLabelText("快手点赞折算系数"), {
      target: { value: "9.999" },
    });
    await openConfirm();
    fireEvent.click(screen.getByRole("button", { name: "确认保存" }));
    const cellError = await screen.findByText("系数须大于 0、不超过 10，最多 3 位小数");
    expect(input("快手点赞折算系数").getAttribute("aria-invalid")).toBe("true");
    expect(input("快手点赞折算系数").getAttribute("aria-describedby")).toBe(cellError.id);
    expect(input("抖音点赞折算系数").getAttribute("aria-invalid")).toBeNull();
    expect(await screen.findByText("有数据不符合要求，请按提示修改")).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(input("快手点赞折算系数")));
    expect(scrollMock).toHaveBeenCalledWith({ block: "nearest", inline: "nearest" });
    expect(scrollMock.mock.instances).toContain(input("快手点赞折算系数"));
    expect(input("快手点赞折算系数").value).toBe("9.999");
    fireEvent.change(input("快手点赞折算系数"), { target: { value: "9.99" } });
    expect(screen.queryByText("系数须大于 0、不超过 10，最多 3 位小数")).toBeNull();
    expect(input("得物爆文阈值").getAttribute("aria-invalid")).toBe("true");
    expect(document.activeElement).toBe(input("快手点赞折算系数"));
  });

  it.each(["saved", "error"] as const)("确认弹窗已关闭后才收到 %s 响应，也正确转移焦点", async (outcome) => {
    let resolve!: (value: PromotionPlatformSettings) => void;
    let reject!: (reason: unknown) => void;
    updateMock.mockReturnValue(new Promise<PromotionPlatformSettings>((done, fail) => {
      resolve = done;
      reject = fail;
    }));
    wrap(<PromotionPlatformSettingTab />);
    fireEvent.change(await screen.findByLabelText("抖音点赞折算系数"), { target: { value: "0.5" } });
    await openConfirm();
    fireEvent.click(screen.getByRole("button", { name: "确认保存" }));
    await waitFor(() => expect(screen.queryByText(CONFIRM_TEXT)).toBeNull());
    await act(async () => {
      if (outcome === "saved") {
        resolve(settings({
          items: ITEMS.map((row) => row.platform === "抖音" ? { ...row, like_coefficient: "0.500" } : row),
          changed: ["抖音"],
        }));
      } else {
        reject({ response: { status: 422, data: {
          code: "VALIDATION_ERROR",
          message: "请求数据校验失败",
          details: { errors: [{ loc: ["body", 1, "like_coefficient"], msg: "校验失败" }] },
        } } });
      }
    });
    await waitFor(() => expect(document.activeElement).toBe(outcome === "saved"
      ? screen.getByRole("heading", { name: "平台指标" })
      : input("抖音点赞折算系数")));
  });

  it("清空的格子先标「必填」，不弹确认也不发请求", async () => {
    wrap(<PromotionPlatformSettingTab />);
    fireEvent.change(await screen.findByLabelText("得物爆文阈值"), { target: { value: "" } });
    fireEvent.click(saveButton());
    expect(await screen.findByText("必填")).toBeTruthy();
    expect(screen.queryByText(CONFIRM_TEXT)).toBeNull();
    expect(updateMock).not.toHaveBeenCalled();
  });

  it("页面文字里没有商品编码", async () => {
    const { container } = wrap(<PromotionPlatformSettingTab />);
    await screen.findByLabelText("小红书点赞折算系数");
    expect(container.textContent).not.toContain("商品编码");
    expect(container.textContent).not.toContain("编码");
  });
});
