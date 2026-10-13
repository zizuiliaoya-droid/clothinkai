// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MiniLineChart } from "./MiniLineChart";

afterEach(cleanup);

const LABELS = ["1", "2", "3", "4", "5"];
// 默认 height 260、padT 16、padB 40 → 0 基线在 y = 220
const ZERO_Y = 220;

function circles(container: HTMLElement) {
  return Array.from(container.querySelectorAll("circle"));
}

describe("MiniLineChart 空值", () => {
  it("null 断线：两段折线，点数 = 非空值个数", () => {
    const { container } = render(
      <MiniLineChart labels={LABELS} series={[{ name: "净投产", color: "#000", data: [1, 2, null, 3, 4] }]} />
    );
    const lines = Array.from(container.querySelectorAll("polyline"));
    expect(lines).toHaveLength(2);
    for (const line of lines) {
      expect(line.getAttribute("points")?.split(" ")).toHaveLength(2);
    }
    expect(circles(container)).toHaveLength(4);
  });

  it("null 不画成 0：没有点落在 0 基线上", () => {
    const { container } = render(
      <MiniLineChart labels={["1", "2", "3"]} series={[{ name: "a", color: "#000", data: [5, null, 5] }]} />
    );
    const cys = circles(container).map((c) => Number(c.getAttribute("cy")));
    expect(cys).toHaveLength(2);
    expect(cys.every((cy) => cy !== ZERO_Y)).toBe(true);
    // 两边各一个孤点，不连线
    expect(container.querySelectorAll("polyline")).toHaveLength(0);
  });

  it("NaN 也当空值", () => {
    const { container } = render(
      <MiniLineChart labels={["1", "2", "3"]} series={[{ name: "a", color: "#000", data: [1, Number.NaN, 2] }]} />
    );
    expect(circles(container)).toHaveLength(2);
    for (const c of circles(container)) {
      expect(Number.isFinite(Number(c.getAttribute("cy")))).toBe(true);
    }
  });

  it("负值落在图内，非负数据的 0 基线不变", () => {
    const { container } = render(
      <MiniLineChart labels={["1", "2"]} series={[{ name: "a", color: "#000", data: [-2, 2] }]} />
    );
    const cys = circles(container).map((c) => Number(c.getAttribute("cy")));
    expect(cys[0]).toBe(ZERO_Y);
    expect(cys[1]).toBeGreaterThanOrEqual(16);
    cleanup();
    const again = render(
      <MiniLineChart labels={["1", "2"]} series={[{ name: "a", color: "#000", data: [0, 4] }]} />
    );
    expect(Number(circles(again.container)[0].getAttribute("cy"))).toBe(ZERO_Y);
  });

  it("fluid：跟着容器宽度画（不超过 width），横轴标签按宽度抽稀；不给 fluid 照旧（评审 r1 L2）", () => {
    // jsdom 没有 ResizeObserver：桩成 observe 时报一次容器宽 320
    class FakeResizeObserver {
      cb: ResizeObserverCallback;
      constructor(cb: ResizeObserverCallback) {
        this.cb = cb;
      }
      observe() {
        this.cb([{ contentRect: { width: 320 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      disconnect() {}
      unobserve() {}
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    try {
      const labels = Array.from({ length: 30 }, (_, i) => `2026-09-${String(i + 1).padStart(2, "0")}`);
      const series = [{ name: "a", color: "#000", data: labels.map((_, i) => i) }];
      const { container } = render(<MiniLineChart fluid width={920} labels={labels} series={series} />);
      const svg = container.querySelector("svg")!;
      expect(svg.getAttribute("width")).toBe("320");
      const xLabels = Array.from(svg.querySelectorAll("text")).filter((t) => t.textContent?.startsWith("2026-"));
      // 内宽 320 - 64 = 256，每个日期标签至少 64px → 最多 4 个
      expect(xLabels.length).toBeLessThanOrEqual(4);
      expect(xLabels.length).toBeGreaterThanOrEqual(2);
      cleanup();
      const fixed = render(<MiniLineChart width={920} labels={labels} series={series} />);
      expect(fixed.container.querySelector("svg")!.getAttribute("width")).toBe("920");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("aria-label 与刻度格式化可传入", () => {
    render(
      <MiniLineChart
        labels={["1", "2"]}
        ariaLabel="单品净投产趋势"
        formatValue={(v) => `${v.toFixed(1)}x`}
        series={[{ name: "a", color: "#000", data: [1, 2] }]}
      />
    );
    expect(screen.getByRole("img", { name: "单品净投产趋势" })).toBeTruthy();
    expect(screen.getByText("2.0x")).toBeTruthy();
  });
});
