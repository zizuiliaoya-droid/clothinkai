// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BarChart } from "./BarChart";
import { ScatterChart } from "./ScatterChart";
import { MiniLineChart } from "../MiniLineChart/MiniLineChart";

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("图表实际容器宽度", () => {
  it("窄屏柱图不把整个640 viewBox缩小，字号与行高保留", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 280, 300));
    const { container } = render(<BarChart title="Top10" valueLabel="净投产" data={[{ key: "one", label: "单品", value: 2 }]} />);
    const svg = screen.getByRole("img", { name: "Top10：1 项" });
    expect(svg.getAttribute("width")).toBe("280");
    expect(svg.getAttribute("viewBox")).toBe("0 0 280 44");
    expect(container.querySelector("text")?.getAttribute("font-size")).toBe("12");
  });

  it("散点占满宽卡片，容器变化后仍保留原点与数据", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 980, 300));
    let resize!: ResizeObserverCallback;
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: ResizeObserverCallback) { resize = callback; }
      observe() {} disconnect() {}
    });
    const { container } = render(<ScatterChart title="散点" xLabel="日期" yLabel="点赞" data={[{ key: "one", label: "博主", x: 1, y: 42 }]} />);
    const svg = screen.getByRole("img", { name: "散点：1 项" });
    expect(svg.getAttribute("width")).toBe("980");
    act(() => resize([{ contentRect: { width: 300 } } as ResizeObserverEntry], {} as ResizeObserver));
    expect(svg.getAttribute("width")).toBe("300");
    expect(container.querySelectorAll("circle")).toHaveLength(1);
    expect(container.querySelector("circle title")?.textContent).toContain("42");
  });

  it("经营趋势在ResizeObserver首次回调前已测宽，无数据后加载也正常", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(new DOMRect(0, 0, 300, 300));
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    const view = render(<MiniLineChart fluid width={920} labels={[]} series={[]} />);
    view.rerender(<MiniLineChart fluid width={920} labels={["今天"]} series={[{ name: "金额", color: "blue", data: [100] }]} />);
    expect(screen.getByRole("img").getAttribute("width")).toBe("300");
  });
});
