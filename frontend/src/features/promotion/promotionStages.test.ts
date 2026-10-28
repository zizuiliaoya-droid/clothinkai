import { describe, expect, it } from "vitest";
import { QUICK_FILTERS, quickCount, stageCountParams, toggleQuickFilter } from "./promotionStages";
import type { PromotionStageCounts } from "./types";

describe("PR10 快捷筛选合同", () => {
  it("14个固定入口，真实零与尚未返回的计数区分", () => {
    expect(QUICK_FILTERS).toHaveLength(14);
    expect(quickCount(undefined, QUICK_FILTERS[0])).toBeUndefined();
    expect(quickCount({ stages: { 待推送仓库: 0 } } as PromotionStageCounts, QUICK_FILTERS[0])).toBe(0);
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
