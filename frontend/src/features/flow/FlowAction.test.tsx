// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { FieldBlock } from "./FieldBlock";
import { FlowAction } from "./FlowAction";
import type { UiState } from "./keys";

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

const UI: UiState = {
  actions: {
    ship_push: { state: "enabled" },
    review: { state: "disabled", reason: "需 PR 主管审核" },
    metrics: { state: "grey", hint: "2026-10-15 起可录" },
  },
  fields: {
    receiver: { state: "edit" },
    quote_amount: { state: "read" },
    shipping: { state: "grey", hint: "推送后由仓库回填" },
  },
};

function button(name: string): HTMLButtonElement {
  return screen.getByRole("button", { name }) as HTMLButtonElement;
}

describe("FlowAction", () => {
  it("没有这个键 → 不渲染", () => {
    const { container } = render(
      <FlowAction ui={UI} actionKey="publish" label="确认发布" onClick={vi.fn()} />,
    );
    expect(container.innerHTML).toBe("");
    const empty = render(<FlowAction ui={undefined} actionKey="publish" label="确认发布" onClick={vi.fn()} />);
    expect(empty.container.innerHTML).toBe("");
  });

  it("enabled → 可点，点击把 UiAction 交给 onClick", () => {
    const onClick = vi.fn();
    render(<FlowAction ui={UI} actionKey="ship_push" label="确认推送仓库" onClick={onClick} />);
    const btn = button("确认推送仓库");
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledWith({ state: "enabled" });
  });

  it("disabled → 禁用，外层 span 是 not-allowed；showReasonBelow 时原因文字在 DOM 里", () => {
    const onClick = vi.fn();
    render(
      <FlowAction ui={UI} actionKey="review" label="审核通过" onClick={onClick} showReasonBelow />,
    );
    const btn = button("审核通过");
    expect(btn.disabled).toBe(true);
    expect((btn.parentElement as HTMLElement).style.cursor).toBe("not-allowed");
    const reason = screen.getByText("需 PR 主管审核");
    expect(btn.getAttribute("aria-describedby")).toBe(reason.id);
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("grey → 禁用 + 灰色提示文字可见", () => {
    const onClick = vi.fn();
    render(<FlowAction ui={UI} actionKey="metrics" label="录 7 天数据" onClick={onClick} />);
    const btn = button("录 7 天数据");
    expect(btn.disabled).toBe(true);
    const hint = screen.getByText("2026-10-15 起可录");
    expect(hint.style.color).not.toBe("");
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });
});

describe("FieldBlock", () => {
  it("不存在 → 不渲染", () => {
    const { container } = render(
      <FieldBlock ui={UI} group="payment_qr" title="收款码">
        值
      </FieldBlock>,
    );
    expect(container.innerHTML).toBe("");
  });

  it("read → 只读显示值，没有编辑按钮", () => {
    render(
      <FieldBlock ui={UI} group="quote_amount" title="报价" onEdit={vi.fn()}>
        ¥300
      </FieldBlock>,
    );
    expect(screen.getByText("¥300")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("grey → 用提示代替值", () => {
    render(
      <FieldBlock ui={UI} group="shipping" title="发货信息">
        SF123
      </FieldBlock>,
    );
    expect(screen.getByText("推送后由仓库回填")).toBeTruthy();
    expect(screen.queryByText("SF123")).toBeNull();
  });

  it("edit → 只读显示值 + 「编辑」按钮", () => {
    const onEdit = vi.fn();
    render(
      <FieldBlock ui={UI} group="receiver" title="收件信息" onEdit={onEdit}>
        张三 138****0000
      </FieldBlock>,
    );
    expect(screen.getByText("张三 138****0000")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "编辑收件信息" }));
    expect(onEdit).toHaveBeenCalledTimes(1);
  });
});
