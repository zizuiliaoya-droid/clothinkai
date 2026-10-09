import { describe, expect, it } from "vitest";
import {
  NEGOTIATION_STATUS_STYLE,
  PROMOTION_SETTLEMENT_STYLE,
  negotiationStatusStyle,
  promotionSettlementStyle,
} from "./stageStyle";

const TABLES = [
  ["谈款状态", NEGOTIATION_STATUS_STYLE, ["草稿", "待审核", "审核通过", "审核驳回"]],
  ["推广单结款状态", PROMOTION_SETTLEMENT_STYLE, ["未核查", "待核查", "待付款", "已付款", "已驳回"]],
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

  it("沿用现有页面的配色", () => {
    expect(negotiationStatusStyle("待审核")).toEqual({ label: "待审核", color: "gold" });
    expect(negotiationStatusStyle("审核驳回")).toEqual({ label: "审核驳回", color: "red" });
    expect(promotionSettlementStyle("已付款")).toEqual({ label: "已付款", color: "green" });
    expect(promotionSettlementStyle("未核查")).toEqual({ label: "未核查", color: "default" });
  });

  it("不认识的值回落为原文 + default，空值回落为「未知」（Tag 上一定有字）", () => {
    expect(negotiationStatusStyle("待主管审核")).toEqual({ label: "待主管审核", color: "default" });
    expect(promotionSettlementStyle("待财务付款")).toEqual({ label: "待财务付款", color: "default" });
    expect(negotiationStatusStyle("")).toEqual({ label: "未知", color: "default" });
    expect(promotionSettlementStyle(null)).toEqual({ label: "未知", color: "default" });
    expect(promotionSettlementStyle(undefined)).toEqual({ label: "未知", color: "default" });
  });
});
