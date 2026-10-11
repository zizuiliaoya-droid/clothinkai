import { describe, expect, it } from "vitest";
import {
  SELF_REVIEW_NOTICE,
  SELF_REVIEW_REASON,
  othersInProgressLines,
  promotionSearchPath,
  receiverConfirmBody,
  receiverPrefill,
  receiverProxyNotice,
  selfReviewNotice,
} from "./negotiationDialogs";

describe("selfReviewNotice（11-24）", () => {
  it("主管待审 + 主管审核因「不能审核自己谈的单」禁用 → 提示找管理员临时授权", () => {
    expect(
      selfReviewNotice({
        status: "主管待审",
        ui: { actions: { manager_review: { state: "disabled", reason: SELF_REVIEW_REASON } } },
      })
    ).toBe(SELF_REVIEW_NOTICE);
  });

  it("PR 提交（缺审核权，原因是「需 PR 主管审核」）、没有这个动作、别的状态都不提示", () => {
    expect(
      selfReviewNotice({
        status: "主管待审",
        ui: { actions: { manager_review: { state: "disabled", reason: "需 PR 主管审核" } } },
      })
    ).toBeNull();
    expect(selfReviewNotice({ status: "主管待审", ui: { actions: {} } })).toBeNull();
    expect(selfReviewNotice({ status: "主管待审", ui: null })).toBeNull();
    expect(
      selfReviewNotice({
        status: "待老板审核",
        ui: { actions: { manager_review: { state: "disabled", reason: SELF_REVIEW_REASON } } },
      })
    ).toBeNull();
  });
});

describe("othersInProgressLines", () => {
  it("一条一行：姓名、状态、更新日期", () => {
    expect(
      othersInProgressLines([
        { pr_name: "王小美", status: "主管待审", updated_at: "2026-10-06T10:00:00+08:00" },
        { pr_name: "李四", status: "草稿", updated_at: "2026-10-01T09:00:00+08:00" },
      ])
    ).toEqual(["王小美也在谈这个博主（主管待审，10-06 更新）", "李四也在谈这个博主（草稿，10-01 更新）"]);
    expect(othersInProgressLines(null)).toEqual([]);
  });
});

describe("receiverPrefill", () => {
  const suggestion = {
    receiver_name: "张三",
    receiver_phone: "13800000000",
    receiver_address: "上海市徐汇区",
    source: "promotion" as const,
    source_label: "推广单 DE2610030A1 · 10-03",
  };

  it("三项都没存过、有建议 → 预填并说明来源；stored 仍是空的（保存时三项都算改动）", () => {
    const p = receiverPrefill({
      receiver_name: null,
      receiver_phone: null,
      receiver_address: null,
      receiver_suggestion: suggestion,
    });
    expect(p.values).toEqual({ receiver_name: "张三", receiver_phone: "13800000000", receiver_address: "上海市徐汇区" });
    expect(p.stored).toEqual({ receiver_name: "", receiver_phone: "", receiver_address: "" });
    expect(p.note).toBe("已带出 推广单 DE2610030A1 · 10-03 的收货信息，请核对");
  });

  it("已存过任一项 → 照存的，不用建议", () => {
    const p = receiverPrefill({
      receiver_name: "李四",
      receiver_phone: null,
      receiver_address: null,
      receiver_suggestion: suggestion,
    });
    expect(p.values.receiver_name).toBe("李四");
    expect(p.values.receiver_phone).toBe("");
    expect(p.note).toBeNull();
  });
});

describe("receiverConfirmBody / receiverProxyNotice / promotionSearchPath", () => {
  it("去首尾空白，空的给 null", () => {
    expect(receiverConfirmBody({ receiver_name: " 张三 ", receiver_phone: "  ", receiver_address: undefined })).toEqual({
      receiver_name: "张三",
      receiver_phone: null,
      receiver_address: null,
    });
  });

  it("不是谈款 PR 本人才写「由你代为确认」", () => {
    const row = { pr_id: "u-pr", pr_name: "王小美" };
    expect(receiverProxyNotice(row, "u-pr")).toBeNull();
    expect(receiverProxyNotice(row, "u-mgr")).toBe("谈款 PR：王小美（由你代为确认）");
    expect(receiverProxyNotice(row, null)).toBeNull();
  });

  it("推广列表按内部编码搜索", () => {
    expect(promotionSearchPath("DE2610030A1")).toBe("/promotions?keyword=DE2610030A1");
  });
});
