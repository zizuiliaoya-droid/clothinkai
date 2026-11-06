// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, expect, it } from "vitest";
import { BloggerLegacyMetrics } from "./BloggerLegacyMetrics";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {writable: true, value: () => ({matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}})});
});
afterEach(cleanup);

it.each([false, true])("缺历史依据不显示正常，疑似=%s", suspected => {
  render(<BloggerLegacyMetrics blogger={{is_suspected_fake: suspected}} />);
  fireEvent.click(screen.getByText("历史第三方指标（已停止更新）"));
  expect(screen.getByText("暂无判定依据")).toBeInTheDocument();
  expect(screen.getByText(suspected ? "疑似 · 历史记录 / 依据待核实" : "未标记（不代表正常）")).toBeInTheDocument();
  expect(screen.getAllByText("暂无数据")).toHaveLength(2);
});

it("展示历史零值和比值，仅显示统计白名单", () => {
  render(<BloggerLegacyMetrics blogger={{
    is_suspected_fake: true, read_like_ratio: "0", audience_profile: {note_stats: {avg_likes: 0, avg_reads: 100}},
    platform_metrics: {raw: {"灰豚指数": "50", "微信号": "must-not-display", "报价": "secret-quote"}},
  }} />);
  fireEvent.click(screen.getByText("历史第三方指标（已停止更新）"));
  expect(screen.getAllByText("0")).toHaveLength(2);
  expect(screen.getByText("50")).toBeInTheDocument();
  expect(screen.queryByText("must-not-display")).not.toBeInTheDocument();
  expect(screen.queryByText("secret-quote")).not.toBeInTheDocument();
});

it("仅有小红书旧快照时展示区间和增长原值，零值与空值区分且不泄漏未知字段", () => {
  render(<BloggerLegacyMetrics blogger={{is_suspected_fake: false, crawler_metrics: {
    "3篇阅读量": 123, "7篇点赞数": 0, "14篇阅读量": null, "3天平均点赞": " ",
    "近期数据涨的博主": "上涨", "微信号": "private-wechat", "报价": "private-quote",
    "unknown": {phone: "private-phone"},
  }}} />);
  fireEvent.click(screen.getByText("历史第三方指标（已停止更新）"));
  expect(screen.getByText("历史记录 / 来源待核实")).toBeInTheDocument();
  expect(screen.queryByText("暂无来源")).not.toBeInTheDocument();
  expect(screen.getByText("历史 · 3篇阅读量")).toBeInTheDocument();
  expect(screen.getByText("123")).toBeInTheDocument();
  expect(screen.getByText("0")).toBeInTheDocument();
  expect(screen.getByText("历史 · 14篇阅读量")).toBeInTheDocument();
  expect(screen.getByText("历史 · 3天平均点赞")).toBeInTheDocument();
  expect(screen.getAllByText("暂无数据")).toHaveLength(4);
  expect(screen.getByText("历史 · 近期数据涨的博主")).toBeInTheDocument();
  expect(screen.getByText("上涨")).toBeInTheDocument();
  expect(screen.getByText(/3 \/ 7 \/ 14 篇/)).toBeInTheDocument();
  expect(screen.queryByText(/3 \/ 7 \/ 14 天/)).not.toBeInTheDocument();
  expect(screen.queryByText(/private-|unknown|微信号|报价/)).not.toBeInTheDocument();
});

it("白名单外原始字段不能成为历史指标或来源依据", () => {
  render(<BloggerLegacyMetrics blogger={{is_suspected_fake: false, crawler_metrics: {"微信号": "private-wechat"}}} />);
  fireEvent.click(screen.getByText("历史第三方指标（已停止更新）"));
  expect(screen.getByText("暂无来源")).toBeInTheDocument();
  expect(screen.queryByText(/private-wechat|微信号/)).not.toBeInTheDocument();
});
