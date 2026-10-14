import { describe, expect, it, vi } from "vitest";
import type { FlowMenuItem } from "@/features/flow/flowMenu";
import type { UiState } from "@/features/flow/keys";
import {
  QR_LOCKED_PAYING,
  QR_LOCKED_SETTLED,
  QR_READONLY_INACTIVE,
  approveConfirmText,
  expandReviewItem,
  metricsBadge,
  paymentQrView,
  reviewOutcomeText,
  settlementImageError,
} from "./settlementFlow";
import type { Promotion, PromotionSettlementBlock } from "./types";

describe("approveConfirmText（审核确认框写应付与去向）", () => {
  it("服务费 0 → 直接记为已结款（有没有结款单、拿不拿得到都一样）", () => {
    expect(approveConfirmText("0.00", null)).toBe("应付 ¥0.00，通过后直接记为已结款");
    expect(approveConfirmText("0.00", "已付款")).toBe("应付 ¥0.00，通过后直接记为已结款");
    expect(approveConfirmText("0.00", undefined)).toBe("应付 ¥0.00，通过后直接记为已结款");
  });
  it("没有结款单、服务费 > 0 → 照旧生成结款单给财务", () => {
    expect(approveConfirmText("400.00", null)).toBe("应付 ¥400.00，通过后生成结款单给财务");
  });
  it("R2 ⑥ 已有导入的已付款结款单 → 转待传结款截图，不写「生成结款单」", () => {
    const text = approveConfirmText("400.00", "已付款");
    expect(text).toBe("应付 ¥400.00，已有导入的已付款结款单，通过后转待传结款截图，不再生成结款单");
    expect(text).not.toContain("给财务");
  });
  it("已有其他状态的结款单 → 不写去向，只写状态", () => {
    expect(approveConfirmText("400.00", "待财务付款")).toBe("应付 ¥400.00，已有结款单（状态：待财务付款）");
  });
  it("拿不到结款信息 → 「确认审核通过？」加服务费，不出现「生成结款单」", () => {
    const text = approveConfirmText("400.00", undefined);
    expect(text).toBe("确认审核通过？应付 ¥400.00");
    expect(text).not.toContain("生成结款单");
  });
  it("读不到报价、没有结款单时不写金额，两种去向都说清", () => {
    expect(approveConfirmText(null, null)).toBe("通过后：应付大于 0 生成结款单给财务，应付为 0 直接记为已结款");
  });
});

describe("reviewOutcomeText（成功提示按 review_outcome）", () => {
  it.each([
    ["settlement_created", "审核通过，已生成结款单，转待财务付款"],
    ["settled_directly", "审核通过。应付为 0，已直接记为已结款"],
    ["imported_settlement_paid", "审核通过。已有导入的已付款结款单，转待传结款截图"],
  ])("%s → %s", (outcome, text) => {
    expect(reviewOutcomeText(outcome)).toBe(text);
  });
  it("没有或不认识 → 只说审核通过", () => {
    expect(reviewOutcomeText(null)).toBe("审核通过");
    expect(reviewOutcomeText(undefined)).toBe("审核通过");
    expect(reviewOutcomeText("toString")).toBe("审核通过");
  });
});

describe("expandReviewItem（审核拆成通过 / 驳回两项）", () => {
  const approve = vi.fn();
  const base = (label: string, disabled: boolean): FlowMenuItem[] => [
    { key: "urge", label: "催发", disabled: false },
    { key: "review", label, disabled, onClick: disabled ? undefined : approve },
    { key: "metrics", label: "录 7 天数据", disabled: false },
  ];

  it("可点、不缺项：通过与驳回都可点，驳回紧跟通过、带 danger", () => {
    const onReject = vi.fn();
    const out = expandReviewItem(base("审核通过", false), { state: "enabled" }, onReject);
    expect(out.map((i) => i.key)).toEqual(["urge", "review", "review-reject", "metrics"]);
    expect(out[1]).toMatchObject({ label: "审核通过", disabled: false });
    expect(out[2]).toMatchObject({ label: "审核驳回", disabled: false, danger: true });
    out[2].onClick?.();
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("可点但缺收款码（in_dialog）：通过禁用并写「缺：收款码」，驳回照常可点", () => {
    const out = expandReviewItem(
      base("审核通过", false),
      { state: "enabled", missing: [{ key: "payment_qr", label: "收款码" }] },
      vi.fn(),
    );
    expect(out[1]).toMatchObject({ key: "review", label: "审核通过（缺：收款码）", disabled: true });
    expect(out[1].onClick).toBeUndefined();
    expect(out[2]).toMatchObject({ label: "审核驳回", disabled: false });
  });

  it("读（PR）：两项都禁用，原因跟着 label", () => {
    const out = expandReviewItem(
      base("审核通过（需 PR 主管审核）", true),
      { state: "disabled", reason: "需 PR 主管审核" },
      vi.fn(),
    );
    expect(out[1]).toMatchObject({ label: "审核通过（需 PR 主管审核）", disabled: true });
    expect(out[2]).toMatchObject({ label: "审核驳回（需 PR 主管审核）", disabled: true });
    expect(out[2].onClick).toBeUndefined();
  });

  it("没有 review 项（隐）：原样返回", () => {
    const items = base("x", false).filter((i) => i.key !== "review");
    expect(expandReviewItem(items, undefined, vi.fn())).toEqual(items);
  });
});

describe("metricsBadge（复盘列读 badges）", () => {
  it("metrics_due → 待录数据；metrics_wait_settle → 满 7 天 · 待结款；都没有 → null", () => {
    expect(metricsBadge(["retro_due", "metrics_due"])?.label).toBe("待录数据");
    expect(metricsBadge(["metrics_wait_settle"])).toMatchObject({ label: "满 7 天 · 待结款", color: "default" });
    expect(metricsBadge(["missing_qr", "retro_due"])).toBeNull();
    expect(metricsBadge(undefined)).toBeNull();
  });
});

describe("paymentQrView（收款码能不能换、只读写什么）", () => {
  const settlement = (status: string): PromotionSettlementBlock => ({
    settlement_id: "s1",
    status,
    amount: null,
    extra_items: [],
    total_amount: null,
    payment_amount: null,
    payment_adjust_note: null,
    payment_date: null,
    reject_reason: null,
  });
  const detail = (
    field: NonNullable<UiState["fields"]>[string] | undefined,
    over: Partial<Promotion> = {},
  ) =>
    ({
      ui: { fields: field ? { payment_qr: field } : {} },
      settlement: null,
      settlement_status: "待核查",
      is_active: true,
      publish_status: "已发布",
      ...over,
    }) as Pick<Promotion, "ui" | "settlement" | "settlement_status" | "is_active" | "publish_status">;

  it("edit → 能换，没有说明", () => {
    expect(paymentQrView(detail({ state: "edit" }))).toEqual({ editable: true, notice: null });
  });
  it("grey → 接口 hint（发布后上传）", () => {
    expect(paymentQrView(detail({ state: "grey", hint: "发布后上传" }, { publish_status: "未发布" }))).toEqual({
      editable: false,
      notice: "发布后上传",
    });
  });
  it("read + 结款单待财务付款 → 付款中锁定", () => {
    const v = paymentQrView(detail({ state: "read" }, { settlement_status: "待付款", settlement: settlement("待财务付款") }));
    expect(v).toEqual({ editable: false, notice: QR_LOCKED_PAYING });
  });
  it.each(["已付款", "已结款"])("read + 推广单 %s → 已结款锁定", (s) => {
    expect(paymentQrView(detail({ state: "read" }, { settlement_status: s })).notice).toBe(QR_LOCKED_SETTLED);
  });
  it("已结款锁定文案：过渡期不写 PR-8 才有的召回 / 寄回运费例外", () => {
    expect(QR_LOCKED_SETTLED).toBe("已结款，收款码已锁定");
    expect(QR_LOCKED_SETTLED).not.toContain("召回");
  });
  it("read + 已停用 / 已取消 → 只读", () => {
    expect(paymentQrView(detail({ state: "read" }, { is_active: false })).notice).toBe(QR_READONLY_INACTIVE);
    expect(paymentQrView(detail({ state: "read" }, { publish_status: "已取消", settlement_status: "未核查" })).notice).toBe(
      QR_READONLY_INACTIVE,
    );
  });
  it("read 但没锁（财务在待主管审核看）→ 只看，没有说明；隐 → 只看", () => {
    expect(paymentQrView(detail({ state: "read" }))).toEqual({ editable: false, notice: null });
    expect(paymentQrView(detail(undefined))).toEqual({ editable: false, notice: null });
  });
  it("能不能换只看 fields：结款单待财务付款但 edit（还没填实付）照样能换", () => {
    expect(paymentQrView(detail({ state: "edit" }, { settlement: settlement("待付款") })).editable).toBe(true);
  });
});

describe("settlementImageError", () => {
  it("格式 / 空 / 超 10MB 都挡，合格返回 null", () => {
    expect(settlementImageError("结款聊天截图", { type: "application/pdf", size: 10 })).toContain("仅支持");
    expect(settlementImageError("结款聊天截图", { type: "image/png", size: 0 })).toContain("不能为空");
    expect(settlementImageError("结款聊天截图", { type: "image/png", size: 10 * 1024 * 1024 + 1 })).toContain("10MB");
    expect(settlementImageError("结款聊天截图", { type: "image/webp", size: 1024 })).toBeNull();
  });
});
