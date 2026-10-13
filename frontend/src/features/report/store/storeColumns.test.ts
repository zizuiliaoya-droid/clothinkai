// 店铺报表周期标签（7e R4 评审 r1 M1 / L1）与「操作」列固定（L4）。
import { isValidElement } from "react";
import { describe, expect, it } from "vitest";
import snapshot from "../../../../../backend/app/modules/report/store_columns.snapshot.json";
import type { StoreReportRow } from "../types";
import { buildStoreColumns, storePeriodLabel } from "./storeColumns";
import type { StoreManualEditing } from "./StoreManualCells";

const span = (date_from: string, date_to: string) => ({ date_from, date_to }) as StoreReportRow;

describe("storePeriodLabel", () => {
  it("日行：YYYY-MM-DD", () => {
    expect(storePeriodLabel(span("2026-09-02", "2026-09-02"), "day")).toBe("2026-09-02");
  });

  it.each([
    ["2026-09-01", "2026-09-30", "2026-09"],
    ["2026-02-01", "2026-02-28", "2026-02"],
    ["2028-02-01", "2028-02-29", "2028-02"],
    ["2026-12-01", "2026-12-31", "2026-12"],
  ])("整月 %s ~ %s：只显示「%s」", (from, to, label) => {
    expect(storePeriodLabel(span(from, to), "month")).toBe(label);
  });

  it.each([
    ["2026-09-14", "2026-09-30", "2026-09（09-14 ~ 09-30）"],
    ["2026-10-01", "2026-10-13", "2026-10（10-01 ~ 10-13）"],
    ["2026-09-01", "2026-09-29", "2026-09（09-01 ~ 09-29）"],
    ["2028-02-01", "2028-02-28", "2028-02（02-01 ~ 02-28）"],
  ])("不完整的月 %s ~ %s：带实际起止「%s」", (from, to, label) => {
    expect(storePeriodLabel(span(from, to), "month")).toBe(label);
  });

  it("周行同年：MM-DD ~ MM-DD", () => {
    expect(storePeriodLabel(span("2026-09-02", "2026-09-06"), "week")).toBe("09-02 ~ 09-06");
  });

  it("周行跨年：起止都带年份", () => {
    expect(storePeriodLabel(span("2025-12-29", "2026-01-04"), "week")).toBe(
      "2025-12-29 ~ 2026-01-04"
    );
  });
});

// 后端常量导出的快照（backend tests/unit/test_store_columns.py 守「文件 = 常量」），这里守「文件 = 前端列定义」：
// 导出类型 store 逐列按它写（7e R6 S11），只改一边就有一边的测试红。
// 后端改了列定义：在 backend 跑 UPDATE_REPORT_SNAPSHOT=1 pytest tests/unit/test_store_columns.py 重写快照，再改这边。
describe("buildStoreColumns 与后端列快照", () => {
  /** 列头文字：字符串原样；HeaderHint 取它的 label。 */
  const titleText = (title: unknown): string =>
    isValidElement<{ label: string }>(title) ? title.props.label : String(title);

  it.each(["day", "week", "month"] as const)("%s：指标列键序与列名 = 快照（周期在前，extra 在后）", (g) => {
    const rows = [{ extra: { 成交人数: "1" } } as unknown as StoreReportRow];
    const cols = buildStoreColumns(g, rows);
    expect(cols[0].key).toBe("period");
    expect(cols[cols.length - 1].key).toBe("ex_成交人数");
    const metric = cols.slice(1, -1);
    expect(snapshot).toHaveLength(23);
    expect(metric.map((c) => c.key)).toEqual(snapshot.map((c) => c.key));
    expect(metric.map((c) => titleText(c.title))).toEqual(snapshot.map((c) => c.header));
  });
});

describe("buildStoreColumns「操作」列", () => {
  const edit = {} as StoreManualEditing;
  const actions = (pinActions: boolean) =>
    buildStoreColumns("day", [], edit, { pinActions }).find((c) => c.key === "actions");

  it("pinActions=true：固定在右侧", () => {
    expect(actions(true)?.fixed).toBe("right");
  });

  it("pinActions=false（窄屏）：不固定", () => {
    expect(actions(false)?.fixed).toBeUndefined();
  });
});
