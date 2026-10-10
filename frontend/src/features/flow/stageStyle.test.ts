import { describe, expect, it } from "vitest";
import {
  NEGOTIATION_STATUS_STYLE,
  PROMOTION_SETTLEMENT_STYLE,
  PROMOTION_STAGES,
  PROMOTION_STAGE_STYLE,
  SHIP_STATUS_STYLE,
  negotiationStatusStyle,
  promotionSettlementStyle,
  promotionStageStyle,
  shipStatusStyle,
} from "./stageStyle";

const TABLES = [
  ["谈款状态", NEGOTIATION_STATUS_STYLE, ["草稿", "待审核", "审核通过", "审核驳回"]],
  ["推广单结款状态", PROMOTION_SETTLEMENT_STYLE, ["未核查", "待核查", "待付款", "已付款", "已驳回"]],
  ["推广单阶段", PROMOTION_STAGE_STYLE, [...PROMOTION_STAGES]],
  ["发货状态", SHIP_STATUS_STYLE, ["待发货", "待打单", "已发货"]],
] as const;

describe("stageStyle", () => {
  it.each(TABLES)("%s：每个值都有非空文案与颜色", (_name, table, values) => {
    expect(Object.keys(table)).toEqual([...values]);
    for (const v of values) {
      const style = (table as Record<string, { label: string; color: string }>)[v];
      expect(style.label.trim()).not.toBe("");
      expect(style.color.trim()).not.toBe("");
    }
  });

  it("推广单阶段 22 个、文案 = 阶段名（与后端同名，不另起界面文案）", () => {
    expect(PROMOTION_STAGES).toHaveLength(22);
    for (const s of PROMOTION_STAGES) expect(PROMOTION_STAGE_STYLE[s].label).toBe(s);
  });

  it("沿用现有页面的配色", () => {
    expect(negotiationStatusStyle("待审核")).toEqual({ label: "待审核", color: "gold" });
    expect(negotiationStatusStyle("审核驳回")).toEqual({ label: "审核驳回", color: "red" });
    expect(promotionSettlementStyle("已付款")).toEqual({ label: "已付款", color: "green" });
    expect(promotionSettlementStyle("未核查")).toEqual({ label: "未核查", color: "default" });
    // 催发 3 档沿用推广列表「是否催发」列的颜色
    expect(promotionStageStyle("催发").color).toBe("orange");
    expect(promotionStageStyle("重要催发").color).toBe("red");
    expect(promotionStageStyle("超时").color).toBe("red");
  });

  it("发货 3 态颜色互不相同（颜色只做辅助，文字仍是状态名）", () => {
    const colors = ["待发货", "待打单", "已发货"].map((s) => shipStatusStyle(s).color);
    expect(new Set(colors).size).toBe(3);
    expect(shipStatusStyle("已发货")).toEqual({ label: "已发货", color: "green" });
  });

  it("不认识的值回落为原文 + default，空值回落为「未知」（Tag 上一定有字）", () => {
    expect(negotiationStatusStyle("待主管审核")).toEqual({ label: "待主管审核", color: "default" });
    expect(promotionSettlementStyle("待财务付款")).toEqual({ label: "待财务付款", color: "default" });
    expect(promotionStageStyle("待老板审核")).toEqual({ label: "待老板审核", color: "default" });
    expect(shipStatusStyle("已打单")).toEqual({ label: "已打单", color: "default" });
    expect(negotiationStatusStyle("")).toEqual({ label: "未知", color: "default" });
    expect(promotionSettlementStyle(null)).toEqual({ label: "未知", color: "default" });
    expect(promotionSettlementStyle(undefined)).toEqual({ label: "未知", color: "default" });
    expect(shipStatusStyle(null)).toEqual({ label: "未知", color: "default" });
  });
});
