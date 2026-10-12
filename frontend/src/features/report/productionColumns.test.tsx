// @vitest-environment jsdom
// 单品投产列定义（7e R3 S8）：页面列键顺序 = 后端 production_columns.PRODUCTION_PAGE_KEYS；
// 一行 = 商品 × 渠道，同商品两渠道在表格里是两个不同的行键。
import { cleanup, render } from "@testing-library/react";
import { Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ProductionRow } from "@/features/report/types";
import {
  PRODUCTION_PAGE_KEYS,
  buildProductionColumns,
  productionRowKey,
} from "./productionColumns";

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

// 照抄 backend/tests/unit/test_production_columns.py 的 _PAGE_KEYS：两边任一改动都要同步改
const BACKEND_PAGE_KEYS = [
  "goods_short_name",
  "style_codes",
  "pay_amount",
  "pay_orders",
  "refund_amount",
  "return_rate",
  "confirmed_amount",
  "promo_cost",
  "ad_spend",
  "total_spend",
  "pre_return_roi",
  "net_roi",
  "unit_deal_cost",
  "add_cart_count",
  "add_cart_cost",
  "ad_add_cart_count",
  "ad_add_cart_cost",
  "offsite_add_cart",
  "recent7_pay_amount",
  "recent7_total_spend",
  "recent7_net_roi",
  "goods_cost_total",
  "gross_profit",
  "gross_margin_rate",
];

const GOODS_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";

function row(over: Partial<ProductionRow>): ProductionRow {
  return {
    goods_id: GOODS_A,
    goods_code: "G-001",
    goods_title: "条纹针织上衣",
    goods_short_name: "条纹上衣",
    is_suit: false,
    style_codes: ["K001"],
    main_image_url: null,
    channel: "普通",
    link_count: 1,
    pay_amount: "500.00",
    refund_amount: "50.00",
    return_rate: "0.1000",
    confirmed_amount: "450.00",
    promo_cost: "100.00",
    ad_spend: "20.00",
    total_spend: "120.00",
    pay_orders: 4,
    add_cart_count: 10,
    add_cart_cost: "12.00",
    ad_add_cart_count: 6,
    ad_add_cart_cost: "3.33",
    offsite_add_cart: 4,
    pre_return_roi: "4.17",
    net_roi: "3.75",
    unit_deal_cost: "30.00",
    recent7_pay_amount: "100.00",
    recent7_total_spend: "20.00",
    recent7_net_roi: "5.00",
    goods_cost_total: null,
    gross_profit: null,
    gross_margin_rate: null,
    extra: {},
    ...over,
  };
}

/** 列键按出现顺序展开（列组的子列也展开）。 */
function columnKeys(columns: ColumnsType<ProductionRow>): string[] {
  return columns.flatMap((c) =>
    "children" in c && c.children
      ? columnKeys(c.children as ColumnsType<ProductionRow>)
      : [String(c.key)]
  );
}

const RANGE = { date_from: "2026-10-05", date_to: "2026-10-11" };

describe("productionColumns 列定义", () => {
  it("页面键常量与后端 PRODUCTION_PAGE_KEYS 逐项相同", () => {
    expect([...PRODUCTION_PAGE_KEYS]).toEqual(BACKEND_PAGE_KEYS);
  });

  it("列顺序 = 页面键（主图在最前、趋势在最后，都是页面专有列）", () => {
    const cols = buildProductionColumns({
      recent7Range: RANGE,
      showMargin: true,
      onTrend: () => {},
    });
    const keys = columnKeys(cols);
    expect(keys[0]).toBe("main_image_url");
    expect(keys[keys.length - 1]).toBe("trend");
    expect(keys.slice(1, -1)).toEqual(BACKEND_PAGE_KEYS);
  });

  it("「待确认收货金额」改名「实际销售金额」", () => {
    const cols = buildProductionColumns({ recent7Range: RANGE, showMargin: true, onTrend: () => {} });
    const confirmed = cols.find((c) => c.key === "confirmed_amount");
    expect(confirmed?.title).toBe("实际销售金额");
    const { container } = render(
      <Table rowKey={productionRowKey} columns={cols} dataSource={[row({})]} pagination={false} />
    );
    expect(container.textContent).toContain("实际销售金额");
    expect(container.textContent).not.toContain("待确认收货金额");
  });

  it("趋势按钮把整行交给 onTrend", () => {
    const onTrend = vi.fn();
    const cols = buildProductionColumns({ recent7Range: RANGE, showMargin: true, onTrend });
    const { getByRole } = render(
      <Table rowKey={productionRowKey} columns={cols} dataSource={[row({})]} pagination={false} />
    );
    getByRole("button", { name: "折线图" }).click();
    expect(onTrend).toHaveBeenCalledWith(expect.objectContaining({ goods_id: GOODS_A }));
  });
});

describe("productionRowKey 行键 = 商品 id + 渠道", () => {
  it("同商品两渠道 → 两个不同的 data-row-key", () => {
    const rows = [
      row({ channel: "普通", link_count: 2 }),
      row({ channel: "直播", pay_amount: "60.00", link_count: 0 }),
    ];
    expect(productionRowKey(rows[0])).toBe(`${GOODS_A}|普通`);
    expect(productionRowKey(rows[1])).toBe(`${GOODS_A}|直播`);
    const cols = buildProductionColumns({ recent7Range: RANGE, showMargin: false, onTrend: () => {} });
    const { container } = render(
      <Table rowKey={productionRowKey} columns={cols} dataSource={rows} pagination={false} />
    );
    const keys = Array.from(container.querySelectorAll("tr[data-row-key]")).map((tr) =>
      tr.getAttribute("data-row-key")
    );
    expect(keys).toEqual([`${GOODS_A}|普通`, `${GOODS_A}|直播`]);
    expect(new Set(keys).size).toBe(2);
    // 两行各自的数，不串行：直播行的支付金额是 60
    expect(container.textContent).toContain("¥60.00");
    expect(container.textContent).toContain("¥500.00");
  });
});
