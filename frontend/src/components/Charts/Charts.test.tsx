// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { BarChart, type BarDatum } from "./BarChart";
import { PieChart } from "./PieChart";
import { ScatterChart, type ScatterDatum } from "./ScatterChart";

beforeAll(() => {
  // antd 的响应式工具要 matchMedia，jsdom 没有
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

function toggle() {
  return screen.getByRole("button", { name: "查看数据" });
}

/** 「查看数据」是原生 button：可 Tab 聚焦，Enter / 空格由浏览器转成 click。 */
function expectKeyboardToggle() {
  const btn = toggle();
  expect(btn.tagName).toBe("BUTTON");
  expect(btn.getAttribute("type")).toBe("button");
  expect(btn.getAttribute("tabindex")).not.toBe("-1");
  btn.focus();
  expect(document.activeElement).toBe(btn);
}

const BARS: BarDatum[] = [
  { key: "a", label: "风衣 A", value: 2.5 },
  { key: "b", label: "空 ROI", value: null },
  { key: "c", label: "NaN", value: Number.NaN },
  { key: "d", label: "无穷", value: Number.POSITIVE_INFINITY },
  { key: "e", label: "亏本款", value: -0.8 },
  { key: "f", label: "未定义", value: undefined },
];

describe("BarChart", () => {
  it("滤掉 null / 非有限值，aria-label 写项数", () => {
    const { container } = render(<BarChart title="ROI Top10" valueLabel="净投产" data={BARS} />);
    expect(screen.getByRole("img", { name: "ROI Top10：2 项" })).toBeTruthy();
    expect(container.querySelectorAll('[data-testid="bar"]')).toHaveLength(2);
    expect(container.querySelectorAll("rect")).toHaveLength(2);
    expect(screen.queryByText("空 ROI")).toBeNull();
  });

  it("「查看数据」切换出带 caption 的表格，再点切回图", () => {
    render(
      <BarChart
        title="ROI Top10"
        valueLabel="净投产"
        labelHeader="商品"
        data={BARS}
        formatValue={(v) => v.toFixed(2)}
        renderLabel={(d) => <a href={`/production?goods_id=${d.key}`}>{d.label}</a>}
      />
    );
    expectKeyboardToggle();
    expect(toggle().getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(toggle());
    expect(toggle().getAttribute("aria-pressed")).toBe("true");
    const table = screen.getByRole("table", { name: "ROI Top10" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["商品", "净投产"]);
    const rows = within(table).getAllByRole("row").slice(1) as HTMLTableRowElement[];
    expect(rows.map((r) => r.textContent)).toEqual(["风衣 A2.50", "亏本款-0.80"]);
    expect(within(table).getByRole("link", { name: "风衣 A" }).getAttribute("href")).toBe("/production?goods_id=a");
    expect(screen.queryByRole("img")).toBeNull();
    fireEvent.click(toggle());
    expect(screen.getByRole("img", { name: "ROI Top10：2 项" })).toBeTruthy();
  });

  it("点击柱子回调；canSelect 为假的不响应", () => {
    const onSelect = vi.fn();
    const { container } = render(
      <BarChart
        title="ROI Top10"
        valueLabel="净投产"
        data={BARS}
        onSelect={onSelect}
        canSelect={(d) => d.key !== "e"}
      />
    );
    const [a, e] = Array.from(container.querySelectorAll<SVGGElement>('[data-testid="bar"]'));
    expect(a.style.cursor).toBe("pointer");
    expect(e.style.cursor).toBe("default");
    fireEvent.click(a);
    fireEvent.click(e);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toMatchObject({ key: "a", value: 2.5 });
  });

  it("全是空值时出 emptyText，没有图和切换按钮", () => {
    render(
      <BarChart title="ROI Top10" valueLabel="净投产" data={[{ key: "x", label: "x", value: null }]} emptyText="没有可比的 ROI" />
    );
    expect(screen.getByText("没有可比的 ROI")).toBeTruthy();
    expect(screen.queryByRole("img", { name: /ROI Top10/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "查看数据" })).toBeNull();
  });
});

describe("PieChart", () => {
  const SLICES = [
    { key: "a", label: "风衣 A", value: 30 },
    { key: "b", label: "其他", value: 10 },
    { key: "c", label: "零", value: 0 },
    { key: "d", label: "负数", value: -5 },
    { key: "e", label: "空", value: null },
    { key: "f", label: "NaN", value: Number.NaN },
  ];

  it("滤掉空值与 ≤ 0，占比按剩下的合计", () => {
    const { container } = render(<PieChart title="支付占比" valueLabel="支付金额" data={SLICES} />);
    expect(screen.getByRole("img", { name: "支付占比：2 项" })).toBeTruthy();
    expect(container.querySelectorAll('[data-testid="slice"] path')).toHaveLength(2);
    fireEvent.click(toggle());
    const table = screen.getByRole("table", { name: "支付占比" });
    const rows = within(table).getAllByRole("row").slice(1) as HTMLTableRowElement[];
    expect(rows.map((r) => Array.from(r.cells).map((c) => c.textContent))).toEqual([
      ["风衣 A", "30", "75.0%"],
      ["其他", "10", "25.0%"],
    ]);
  });

  it("点击扇区回调；「其他」不可点", () => {
    const onSelect = vi.fn();
    const { container } = render(
      <PieChart
        title="支付占比"
        valueLabel="支付金额"
        data={SLICES}
        onSelect={onSelect}
        canSelect={(d) => d.key !== "b"}
      />
    );
    const [a, other] = Array.from(container.querySelectorAll<SVGGElement>('[data-testid="slice"]'));
    fireEvent.click(other);
    fireEvent.click(a);
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onSelect.mock.calls[0][0]).toMatchObject({ key: "a" });
    expect(other.style.cursor).toBe("default");
  });

  it("只剩一项画整圆；切换按钮可键盘到达；全空出 emptyText", () => {
    const { container } = render(
      <PieChart title="支付占比" valueLabel="支付金额" data={[SLICES[0], SLICES[4]]} />
    );
    expect(container.querySelectorAll('[data-testid="slice"] circle')).toHaveLength(1);
    expectKeyboardToggle();
    cleanup();
    render(<PieChart title="支付占比" valueLabel="支付金额" data={[SLICES[2], SLICES[3]]} emptyText="区间内没有支付" />);
    expect(screen.getByText("区间内没有支付")).toBeTruthy();
    expect(screen.queryByRole("img", { name: /支付占比/ })).toBeNull();
  });
});

describe("ScatterChart", () => {
  const POINTS: ScatterDatum[] = [
    { key: "p1", label: "博主甲", x: 1, y: 100, size: 100 },
    { key: "p2", label: "博主乙", x: 2, y: 50, size: 25 },
    { key: "p3", label: "博主丙", x: 3, y: 80, size: null },
    { key: "p4", label: "博主丁", x: 4, y: 10, size: 0 },
    { key: "n1", label: "没发布日", x: null, y: 30, size: 10 },
    { key: "n2", label: "没点赞", x: 5, y: null, size: 10 },
    { key: "n3", label: "NaN", x: 6, y: Number.NaN },
  ];

  function radii(container: HTMLElement) {
    return Array.from(container.querySelectorAll('[data-testid="point"]')).map((c) => Number(c.getAttribute("r")));
  }

  it("x 或 y 为空的点不画；半径 √ 映射 4 ~ 14，size 为空用 6", () => {
    const { container } = render(
      <ScatterChart title="笔记散点" xLabel="发布日期" yLabel="点赞" data={POINTS} />
    );
    expect(screen.getByRole("img", { name: "笔记散点：4 项" })).toBeTruthy();
    expect(radii(container)).toEqual([14, 9, 6, 4]);
    for (const c of Array.from(container.querySelectorAll('[data-testid="point"]'))) {
      expect(Number.isFinite(Number(c.getAttribute("cx")))).toBe(true);
      expect(Number.isFinite(Number(c.getAttribute("cy")))).toBe(true);
    }
  });

  it("表格带点大小列，空值写「—」；悬停提示可自定义", () => {
    const { container } = render(
      <ScatterChart
        title="笔记散点"
        xLabel="发布日期"
        yLabel="点赞"
        sizeLabel="站外推广成本"
        data={POINTS}
        pointTitle={() => "详情页随推广单时间线上线"}
      />
    );
    const titles = Array.from(container.querySelectorAll('[data-testid="point"] title')).map((t) => t.textContent);
    expect(titles).toEqual(Array(4).fill("详情页随推广单时间线上线"));
    fireEvent.click(toggle());
    const table = screen.getByRole("table", { name: "笔记散点" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "名称",
      "发布日期",
      "点赞",
      "站外推广成本",
    ]);
    const rows = within(table).getAllByRole("row").slice(1) as HTMLTableRowElement[];
    expect(rows).toHaveLength(4);
    expect(rows[2].cells[3].textContent).toBe("—");
  });

  it("点击回调；不给 onSelect 时点不可点", () => {
    const onSelect = vi.fn();
    const { container } = render(
      <ScatterChart title="笔记散点" xLabel="发布日期" yLabel="点赞" data={POINTS} onSelect={onSelect} />
    );
    const first = container.querySelector<SVGCircleElement>('[data-testid="point"]')!;
    expect(first.style.cursor).toBe("pointer");
    fireEvent.click(first);
    expect(onSelect.mock.calls[0][0]).toMatchObject({ key: "p1" });
    cleanup();
    const again = render(<ScatterChart title="笔记散点" xLabel="发布日期" yLabel="点赞" data={POINTS} />);
    expect(again.container.querySelector<SVGCircleElement>('[data-testid="point"]')!.style.cursor).toBe("default");
    expectKeyboardToggle();
  });

  it("横轴最后一个刻度右对齐，不超出右边（评审 r1 L2）；只有一个刻度时居中", () => {
    const { container } = render(
      <ScatterChart title="笔记散点" xLabel="发布日期" yLabel="点赞" data={POINTS} />
    );
    const anchors = (root: HTMLElement) =>
      Array.from(root.querySelectorAll('[data-testid="x-tick"]')).map((t) => t.getAttribute("text-anchor"));
    expect(anchors(container)).toEqual(["middle", "middle", "end"]);
    cleanup();
    const single = render(
      <ScatterChart title="笔记散点" xLabel="发布日期" yLabel="点赞" data={POINTS.slice(0, 1)} />
    );
    expect(anchors(single.container)).toEqual(["middle"]);
  });

  it("全被滤掉时出 emptyText", () => {
    render(
      <ScatterChart
        title="笔记散点"
        xLabel="发布日期"
        yLabel="点赞"
        data={POINTS.slice(4)}
        emptyText="还没有录 7 天数据的笔记"
      />
    );
    expect(screen.getByText("还没有录 7 天数据的笔记")).toBeTruthy();
    expect(screen.queryByRole("img", { name: /笔记散点/ })).toBeNull();
  });
});
