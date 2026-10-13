// 店铺报表周期标签（7e R4 评审 r1 M1 / L1）与「操作」列固定（L4）。
import { describe, expect, it } from "vitest";
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
