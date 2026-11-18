import { describe, expect, it } from "vitest";
import { QUICK_FILTERS, STAGE_FILTERS, quickCount, stageCountParams, toggleQuickFilter } from "./promotionStages";
import type { PromotionStageCounts } from "./types";

describe("PR10 快捷筛选合同", () => {
  it("15个固定入口，真实零与尚未返回的计数区分", () => {
    expect(QUICK_FILTERS).toHaveLength(15);
    expect(quickCount(undefined, QUICK_FILTERS[0])).toBeUndefined();
    expect(quickCount({ stages: { 待推送仓库: 0 } } as PromotionStageCounts, QUICK_FILTERS[0])).toBe(0);
  });
  it("档期内在超时前，阶段快捷项与下拉共用阶段对象，保留角标和完结入口", () => {
    const scheduled = QUICK_FILTERS.find((item) => item.label === "档期内")!;
    expect(QUICK_FILTERS.indexOf(scheduled) + 1).toBe(QUICK_FILTERS.findIndex((item) => item.label === "超时"));
    for (const item of QUICK_FILTERS.filter((item) => "stage" in item)) expect(STAGE_FILTERS).toContain(item);
    expect(quickCount({ stages: { 档期内: 3 } } as PromotionStageCounts, scheduled)).toBe(3);
    expect(toggleQuickFilter({ badge: "metrics_due", page: 2 }, scheduled)).toEqual({ stage: "档期内", badge: undefined, completed: undefined, page: 1 });
    expect(QUICK_FILTERS).toContainEqual({ label: "缺收款码", badge: "missing_qr" });
    expect(QUICK_FILTERS).toContainEqual({ label: "已完结", completed: true });
  });
  it("单选且可取消，换阶段清旧角标并回第一页", () => {
    expect(toggleQuickFilter({ badge: "metrics_due", page: 3 }, QUICK_FILTERS[0])).toEqual({ stage: "待推送仓库", badge: undefined, completed: undefined, page: 1 });
    expect(toggleQuickFilter({ stage: "待推送仓库" }, QUICK_FILTERS[0]).stage).toBeUndefined();
    expect(toggleQuickFilter({ completed: true }, QUICK_FILTERS[0]).completed).toBeUndefined();
  });
  it("计数去掉stage/badge/分页，保留完整交集及启用/完结，不改原对象", () => {
    const filters = { stage: "待主管审核" as const, badge: "missing_qr" as const, page: 2, page_size: 10, completed: false, is_active: false, receiver_phone: "138", seasons: ["春"], keyword: "单号" };
    expect(stageCountParams(filters)).toEqual({ completed: false, is_active: false, receiver_phone: "138", seasons: ["春"], keyword: "单号" });
    expect(filters.page).toBe(2);
  });
});
