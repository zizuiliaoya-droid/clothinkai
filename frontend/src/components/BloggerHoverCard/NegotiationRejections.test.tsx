// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import type { NegotiationRejectionItem } from "@/features/negotiation/types";
import { rejectionMetaLine, rejectionOpinion } from "@/features/negotiation/bloggerRejections";
import { NegotiationRejections } from "./NegotiationRejections";

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

const STYLE_CODE = "FH-2026-0815";
const LONG_OPINION = "报价比上次同类合作高出一截，博主近 30 天的数据也在掉，先压到 300 以内再提；另外约定发布时间离上新太近，往后挪一周。";

function rejection(overrides: Partial<NegotiationRejectionItem> = {}): NegotiationRejectionItem {
  return {
    negotiation_id: "n-1",
    pr_name: "李四",
    rejected_at: "2026-10-08T10:30:00+08:00",
    style_code: STYLE_CODE,
    display_short_name: "飞狐短裙",
    platform: "小红书",
    quote_amount: "350",
    opinion: LONG_OPINION,
    ...overrides,
  };
}

describe("rejectionMetaLine / rejectionOpinion", () => {
  it("时间 · 提报人 · 商品简称 · 平台 · 服务费，不含款号", () => {
    const line = rejectionMetaLine(rejection());
    expect(line).toBe("2026-10-08 · 李四 · 飞狐短裙 · 小红书 · ¥350.00");
    expect(line).not.toContain(STYLE_CODE);
  });

  it("服务费没有权限（null）显示「—」，0 照常显示；缺项写「—」", () => {
    expect(rejectionMetaLine(rejection({ quote_amount: null }))).toBe("2026-10-08 · 李四 · 飞狐短裙 · 小红书 · —");
    expect(rejectionMetaLine(rejection({ quote_amount: "0" }))).toMatch(/· ¥0\.00$/);
    expect(rejectionMetaLine(rejection({ pr_name: null, display_short_name: null, platform: null }))).toBe(
      "2026-10-08 · — · — · — · ¥350.00",
    );
  });

  it("意见去两端空白，空的回 null", () => {
    expect(rejectionOpinion(rejection({ opinion: "  价高  " }))).toBe("价高");
    expect(rejectionOpinion(rejection({ opinion: "   " }))).toBeNull();
    expect(rejectionOpinion(rejection({ opinion: null }))).toBeNull();
  });
});

describe("NegotiationRejections", () => {
  it("没有驳回 → 写「没有被驳回的谈款」，不出「共 N 次」", () => {
    render(<NegotiationRejections data={{ rejections: [], total_rejections: 0 }} loading={false} />);
    const section = screen.getByRole("region", { name: "谈款驳回记录" });
    expect(section.textContent).toContain("没有被驳回的谈款");
    expect(section.textContent).not.toContain("共");
  });

  it("每条一行说明 + 意见（两行省略、悬停全文）；标题旁「共 N 次」用总数；页面文字无款号", () => {
    const rows = [
      rejection(),
      rejection({ negotiation_id: null, pr_name: "王五", quote_amount: null, opinion: null }),
    ];
    render(<NegotiationRejections data={{ rejections: rows, total_rejections: 7 }} loading={false} />);
    const section = screen.getByRole("region", { name: "谈款驳回记录" });
    expect(section.textContent).toContain("共 7 次");
    const items = within(section).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0].textContent).toContain("2026-10-08 · 李四 · 飞狐短裙 · 小红书 · ¥350.00");
    const opinion = within(items[0]).getByTitle(LONG_OPINION);
    expect(opinion.textContent).toBe(LONG_OPINION);
    expect(items[1].textContent).toContain("王五 · 飞狐短裙 · 小红书 · —");
    expect(items[1].textContent).toContain("没有填写驳回意见");
    expect(section.textContent).not.toContain(STYLE_CODE);
  });

  it("加载中不显示「没有被驳回的谈款」", () => {
    render(<NegotiationRejections data={undefined} loading />);
    expect(screen.getByRole("region", { name: "谈款驳回记录" }).textContent).not.toContain("没有被驳回的谈款");
  });
});
