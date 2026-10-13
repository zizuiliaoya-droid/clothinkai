import { describe, expect, it } from "vitest";
import { NEGOTIATION_STATUSES } from "@/features/negotiation/types";
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
  ["谈款状态", NEGOTIATION_STATUS_STYLE, [...NEGOTIATION_STATUSES]],
  [
    "推广单结款状态",
    PROMOTION_SETTLEMENT_STYLE,
    ["未核查", "待核查", "待付款", "已付款", "已驳回", "结款驳回", "已结款"],
  ],
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

  it("谈款 7 态：存储值顺序 = Tab 顺序，文案 = 设计 3.2「界面显示」列，颜色互不相同", () => {
    // 存储值与后端 NegotiationStatus 一致（后端没有导出快照，这里照抄 enums.py）
    expect([...NEGOTIATION_STATUSES]).toEqual([
      "主管待审",
      "待老板审核",
      "老板退回",
      "待补收货地址",
      "草稿",
      "审核驳回",
      "已定稿",
    ]);
    expect(Object.fromEntries(NEGOTIATION_STATUSES.map((s) => [s, negotiationStatusStyle(s).label]))).toEqual({
      主管待审: "谈款中 · 主管待审",
      待老板审核: "谈款中 · 待老板审核",
      老板退回: "谈款中 · 老板退回",
      待补收货地址: "待补收货地址",
      草稿: "草稿",
      审核驳回: "审批未通过",
      已定稿: "已定稿",
    });
    const colors = NEGOTIATION_STATUSES.map((s) => negotiationStatusStyle(s).color);
    expect(new Set(colors).size).toBe(7);
    expect(negotiationStatusStyle("审核驳回").color).toBe("red");
    expect(negotiationStatusStyle("已定稿").color).toBe("green");
  });

  it("推广单结款状态 7 个：文案 = 3.4「界面显示」列，配色与同名阶段一致", () => {
    const values = ["未核查", "待核查", "待付款", "已付款", "已驳回", "结款驳回", "已结款"] as const;
    expect(Object.fromEntries(values.map((s) => [s, promotionSettlementStyle(s).label]))).toEqual({
      未核查: "未发布",
      待核查: "待主管审核",
      待付款: "待财务付款",
      已付款: "待传结款截图",
      已驳回: "推广驳回 · 待重提",
      结款驳回: "结款驳回 · 待重提",
      已结款: "已结款",
    });
    expect(promotionSettlementStyle("未核查").color).toBe("default");
    expect(promotionSettlementStyle("已结款").color).toBe("green");
    // 其余 5 个文案就是阶段名：颜色跟阶段一致
    for (const s of ["待核查", "待付款", "已付款", "已驳回", "结款驳回"] as const) {
      const { label, color } = promotionSettlementStyle(s);
      expect(color).toBe(promotionStageStyle(label).color);
    }
  });

  it("沿用现有页面的配色", () => {
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
    // 旧 4 态的「审核通过」「待审核」迁移后不再出现，出现了也照原文显示
    expect(negotiationStatusStyle("审核通过")).toEqual({ label: "审核通过", color: "default" });
    expect(promotionSettlementStyle("待结算")).toEqual({ label: "待结算", color: "default" });
    expect(promotionStageStyle("待老板审核")).toEqual({ label: "待老板审核", color: "default" });
    expect(shipStatusStyle("已打单")).toEqual({ label: "已打单", color: "default" });
    expect(negotiationStatusStyle("")).toEqual({ label: "未知", color: "default" });
    expect(promotionSettlementStyle(null)).toEqual({ label: "未知", color: "default" });
    expect(promotionSettlementStyle(undefined)).toEqual({ label: "未知", color: "default" });
    expect(shipStatusStyle(null)).toEqual({ label: "未知", color: "default" });
  });
});
