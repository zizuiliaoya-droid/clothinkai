// @vitest-environment jsdom
// 仓库页（流程线 8.5）：数据走 /api/warehouse/shipments，导出按页级 ui，回填按行 ui，颜色尺码按明细 / 旧单原文。
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { WarehouseShipmentPage, WarehouseShipmentRow } from "./types";

const listMock = vi.fn();
vi.mock("@/features/warehouse/api", () => ({
  listWarehouseShipments: (...args: unknown[]) => listMock(...args),
  updateWarehouseWaybill: vi.fn(),
  exportWarehouseShipments: vi.fn(),
}));

import { WarehousePage } from "@/pages/WarehousePage";

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

beforeEach(() => listMock.mockReset());
afterEach(cleanup);

function row(over: Partial<WarehouseShipmentRow>): WarehouseShipmentRow {
  return {
    id: "p1",
    internal_code: "PR-001",
    style_code: "ST-1",
    display_short_name: "上衣",
    goods_title: "条纹上衣",
    items: [],
    legacy_color_spec: null,
    receiver_name: "张三",
    receiver_phone: "13800000000",
    receiver_address: "上海市徐汇区某路 1 号",
    receiver_updated_after_push: false,
    items_updated_after_push: false,
    ship_status: "待打单",
    ship_pushed_at: "2026-10-09T08:00:00+08:00",
    ship_pushed_by_name: "主管甲",
    ship_courier: null,
    ship_waybill: null,
    shipped_at: null,
    ui: { actions: { ship_fill: { state: "enabled" } } },
    ...over,
  };
}

function pageOf(rows: WarehouseShipmentRow[], exportable: boolean): WarehouseShipmentPage {
  return {
    items: rows,
    total: rows.length,
    page: 1,
    page_size: 20,
    couriers: ["顺丰", "中通", "其他"],
    ui: { actions: exportable ? { export: { state: "enabled" } } : {} },
  };
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <WarehousePage />
    </QueryClientProvider>
  );
}

describe("WarehousePage", () => {
  it("标题、三个桶，默认按待打单调仓库专用接口", async () => {
    listMock.mockResolvedValue(pageOf([row({})], true));
    renderPage();
    expect(await screen.findByText("仓库发货")).toBeTruthy();
    for (const b of ["待打单", "已发货", "全部"]) expect(screen.getAllByText(b).length).toBeGreaterThan(0);
    expect(screen.queryByText("已打单")).toBeNull();
    expect(listMock).toHaveBeenCalledWith(expect.objectContaining({ bucket: "待打单", page: 1 }));
  });

  it("套装每个成员一行颜色尺码；旧单显示原文 + 「旧」；不出现 SKU 编码", async () => {
    listMock.mockResolvedValue(
      pageOf(
        [
          row({
            items: [
              { display_short_name: "上衣", color: "黑色", size: "M" },
              { display_short_name: "阔腿裤", color: "白色", size: "L" },
            ],
          }),
          row({ id: "p2", internal_code: "PR-002", legacy_color_spec: "红色 XL" }),
        ],
        true
      )
    );
    renderPage();
    expect(await screen.findByText("上衣 · 黑色 / M")).toBeTruthy();
    expect(screen.getByText("阔腿裤 · 白色 / L")).toBeTruthy();
    const legacy = screen.getByText("红色 XL");
    expect(within(legacy).getByText("旧")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/SKU-/);
  });

  it("收件三项、推送人；推送后改过地址 / 明细出文字 Tag", async () => {
    listMock.mockResolvedValue(
      pageOf(
        [
          row({
            items: [{ display_short_name: "上衣", color: "黑色", size: "M" }],
            receiver_updated_after_push: true,
            items_updated_after_push: true,
          }),
        ],
        true
      )
    );
    renderPage();
    expect(await screen.findByText("张三")).toBeTruthy();
    expect(screen.getByText("13800000000")).toBeTruthy();
    expect(screen.getByText("地址已更新")).toBeTruthy();
    expect(screen.getByText("明细已更新")).toBeTruthy();
  });

  it("导出按钮只在页级 ui.actions.export 存在时出现，当前桶 0 条禁用", async () => {
    listMock.mockResolvedValue(pageOf([row({})], false));
    const { unmount } = renderPage();
    expect(await screen.findByText("PR-001")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /导出待打单 Excel/ })).toBeNull();
    unmount();

    listMock.mockResolvedValue(pageOf([], true));
    renderPage();
    const btn = await screen.findByRole("button", { name: /导出待打单 Excel/ });
    expect((btn as HTMLButtonElement).disabled).toBe(true);
  });

  it("回填按钮按行 ui：待打单「回填」、已发货「改单号」、没有 ship_fill 不出按钮", async () => {
    listMock.mockResolvedValue(
      pageOf(
        [
          row({}),
          row({ id: "p2", internal_code: "PR-002", ship_status: "已发货", ship_waybill: "SF9", ship_courier: "顺丰" }),
          row({ id: "p3", internal_code: "PR-003", ship_status: "已发货", ui: { actions: {} } }),
        ],
        true
      )
    );
    renderPage();
    expect(await screen.findByRole("button", { name: "回填" })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "改单号" })).toHaveLength(1);
  });

  it("回填弹窗：顶部只读收件三项与颜色尺码，快递公司选项来自接口", async () => {
    listMock.mockResolvedValue(
      pageOf([row({ items: [{ display_short_name: "上衣", color: "黑色", size: "M" }] })], true)
    );
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "回填" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("张三")).toBeTruthy();
    expect(within(dialog).getByText("上海市徐汇区某路 1 号")).toBeTruthy();
    expect(within(dialog).getByText("黑色 / M")).toBeTruthy();
    expect(within(dialog).getByLabelText("快递公司")).toBeTruthy();
    expect(within(dialog).getByLabelText("单号")).toBeTruthy();
    expect(within(dialog).getByLabelText("发货时间")).toBeTruthy();
  });
});
