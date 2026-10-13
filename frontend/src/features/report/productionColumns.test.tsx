// @vitest-environment jsdom
// 单品投产列定义（7e R3 S8）：页面列键顺序与列名 = 后端快照 production_columns.snapshot.json
// （7e R4 S9：不再手抄，列名只许例外表里的列不同）；一行 = 商品 × 渠道，同商品两渠道在表格里是两个不同的行键。
// 后端改了列定义：在 backend 跑 UPDATE_REPORT_SNAPSHOT=1 pytest tests/unit/test_production_columns.py 重写快照，再改这边。
import { cleanup, fireEvent, render, within } from "@testing-library/react";
import { Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { ProductionRow } from "@/features/report/types";
import snapshot from "../../../../backend/app/modules/report/production_columns.snapshot.json";
import {
  PRODUCTION_PAGE_KEYS,
  PRODUCTION_TITLE_EXCEPTIONS,
  buildProductionColumns,
  productionColumnTitle,
  productionColumnsWidth,
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

// 后端常量导出的快照（backend tests/unit/test_production_columns.py 守「文件 = 常量」），
// 这里守「文件 = 前端列定义」：只改一边就有一边的测试红（R3 评审 L5）
const BACKEND_PAGE_KEYS = snapshot.map((c) => c.key);

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
  it("页面键常量与后端快照逐项相同（顺序也比）", () => {
    expect(BACKEND_PAGE_KEYS).toHaveLength(24);
    expect([...PRODUCTION_PAGE_KEYS]).toEqual(BACKEND_PAGE_KEYS);
  });

  it("列名 = 快照表头，只有例外表里的列不同（L1）", () => {
    const differing = snapshot
      .filter((c) => productionColumnTitle(c.key as (typeof PRODUCTION_PAGE_KEYS)[number]) !== c.header)
      .map((c) => c.key);
    expect(differing).toEqual(Object.keys(PRODUCTION_TITLE_EXCEPTIONS));
    expect(differing).toEqual(["goods_short_name", "pay_orders"]);
    // 例外两列：页面「商品」「支付件数」，导出「商品简称」「支付件数（不扣刷单）」
    expect(productionColumnTitle("goods_short_name")).toBe("商品");
    expect(productionColumnTitle("pay_orders")).toBe("支付件数");
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

describe("productionColumns 显示（S9）", () => {
  it("近 7 天三列在一个列组下，列组表头「近 7 天（MM-DD ~ MM-DD）」，子列不再带前缀", () => {
    const cols = buildProductionColumns({ recent7Range: RANGE, showMargin: false, onTrend: () => {} });
    const group = cols.find((c) => c.key === "recent7");
    expect(group?.title).toBe("近 7 天（10-05 ~ 10-11）");
    const children = ("children" in group! ? group.children : []) as ColumnsType<ProductionRow>;
    expect(children.map((c) => c.key)).toEqual([
      "recent7_pay_amount",
      "recent7_total_spend",
      "recent7_net_roi",
    ]);
    expect(children.map((c) => c.title)).toEqual(["支付金额", "推广总花费", "净投产比"]);
  });

  it("毛利三列按 showMargin 显示 / 隐藏", () => {
    const on = columnKeys(buildProductionColumns({ recent7Range: RANGE, showMargin: true, onTrend: () => {} }));
    const off = columnKeys(buildProductionColumns({ recent7Range: RANGE, showMargin: false, onTrend: () => {} }));
    const margin = ["goods_cost_total", "gross_profit", "gross_margin_rate"];
    margin.forEach((k) => expect(on).toContain(k));
    margin.forEach((k) => expect(off).not.toContain(k));
  });

  it("商品列每行有渠道标签；link_count ≥ 2 才标「N 条链接」", () => {
    const rows = [
      row({ channel: "普通", link_count: 3 }),
      row({ channel: "直播", link_count: 1 }),
    ];
    const cols = buildProductionColumns({ recent7Range: RANGE, showMargin: false, onTrend: () => {} });
    const { container } = render(
      <Table rowKey={productionRowKey} columns={cols} dataSource={rows} pagination={false} />
    );
    const trs = Array.from(container.querySelectorAll("tbody tr[data-row-key]"));
    expect(trs[0].textContent).toContain("普通");
    expect(trs[0].textContent).toContain("3 条链接");
    expect(trs[1].textContent).toContain("直播");
    expect(trs[1].textContent).not.toContain("条链接");
  });

  it("站外加购：null 显示「—」带说明入口，有数照出", () => {
    const cols = buildProductionColumns({ recent7Range: RANGE, showMargin: false, onTrend: () => {} });
    const { container, getAllByTestId } = render(
      <Table
        rowKey={productionRowKey}
        columns={cols}
        dataSource={[row({ offsite_add_cart: null }), row({ channel: "直播", offsite_add_cart: 0 })]}
        pagination={false}
      />
    );
    const empties = getAllByTestId("offsite-add-cart-empty");
    expect(empties).toHaveLength(1);
    expect(empties[0].textContent).toBe("—");
    // 有数的那行（0 也是数）不带说明
    const live = container.querySelector(`tr[data-row-key="${GOODS_A}|直播"]`);
    expect(live?.querySelector('[data-testid="offsite-add-cart-empty"]')).toBeNull();
  });

  it("支付件数列头带说明「剔除刷单开关不扣件数」：悬停列头文字出 Tooltip，读屏读隐藏说明", async () => {
    const cols = buildProductionColumns({ recent7Range: RANGE, showMargin: false, onTrend: () => {} });
    const { container, findByText } = render(
      <Table rowKey={productionRowKey} columns={cols} dataSource={[row({})]} pagination={false} />
    );
    const thead = container.querySelector("thead") as HTMLElement;
    expect(thead.textContent).toContain("支付件数（剔除刷单开关不扣件数）");
    fireEvent.mouseEnter(within(thead).getByText("支付件数"));
    expect(await findByText("剔除刷单开关不扣件数")).toBeTruthy();
  });

  it("支付件数列头说明 Tab 只停一次：列头里一个，测量行里的克隆不可聚焦；聚焦出 Tooltip（L4）", async () => {
    const cols = buildProductionColumns({ recent7Range: RANGE, showMargin: true, onTrend: () => {} });
    const { container, findByText } = render(
      <Table
        rowKey={productionRowKey}
        columns={cols}
        dataSource={[row({})]}
        scroll={{ x: productionColumnsWidth(cols) }}
        pagination={false}
      />
    );
    // 场景有效性：横向滚动时测量行确实克隆了支付件数列头
    const measure = container.querySelector('tr[aria-hidden="true"]') as HTMLElement;
    expect(measure?.textContent).toContain("支付件数");
    const stops = Array.from(container.querySelectorAll<HTMLElement>("[tabindex]")).filter(
      (el) => el.tabIndex >= 0
    );
    expect(stops).toHaveLength(1);
    expect(stops[0].closest("thead")).not.toBeNull();
    expect(stops[0].textContent).toContain("支付件数");
    expect(measure.querySelectorAll('[tabindex="0"]')).toHaveLength(0);
    fireEvent.focus(stops[0]);
    expect(await findByText("剔除刷单开关不扣件数")).toBeTruthy();
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
