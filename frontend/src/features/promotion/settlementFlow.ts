// 推广列表里审核与结款相关的文案和判断（流程线 PR-6，设计 8.4）。纯函数，vitest 测。
//
// 原则：能不能做一律看接口的 ui / review_outcome / badges，不再按 settlement_status === "已付款" 推。
import type { FlowMenuItem } from "@/features/flow/flowMenu";
import type { UiAction } from "@/features/flow/keys";
import type { Promotion, ReviewOutcome } from "./types";

/**
 * 审核通过确认框：写清应付与去向（R2 / R3 / R2 ⑥，与后端 review 的判断顺序一致）。
 * - 服务费 = 0 → 直接已结款（后端先判服务费，与有没有结款单无关）
 * - 服务费 > 0：按行上的 existing_settlement_status 写——null 没有结款单 → 生成结款单给财务；
 *   已付款（导入的）→ 转待传结款截图；其他状态 → 只写「已有结款单（状态：X）」（后端会 409）；
 *   undefined 拿不到 → 不写去向，只写「确认审核通过？」加服务费
 */
export function approveConfirmText(
  quoteAmount: string | null | undefined,
  existingSettlementStatus: string | null | undefined,
): string {
  const hasFee = quoteAmount != null && quoteAmount !== "";
  if (hasFee && !(Number(quoteAmount) > 0)) return `应付 ¥${quoteAmount}，通过后直接记为已结款`;
  const fee = hasFee ? `应付 ¥${quoteAmount}` : null;
  if (existingSettlementStatus === undefined) {
    return fee ? `确认审核通过？${fee}` : "确认审核通过？";
  }
  if (existingSettlementStatus === "已付款") {
    return `${fee ? `${fee}，` : ""}已有导入的已付款结款单，通过后转待传结款截图，不再生成结款单`;
  }
  if (existingSettlementStatus !== null) {
    return `${fee ? `${fee}，` : ""}已有结款单（状态：${existingSettlementStatus}）`;
  }
  return fee
    ? `${fee}，通过后生成结款单给财务`
    : "通过后：应付大于 0 生成结款单给财务，应付为 0 直接记为已结款";
}

const OUTCOME_TEXT: Record<ReviewOutcome, string> = {
  settlement_created: "审核通过，已生成结款单，转待财务付款",
  settled_directly: "审核通过。应付为 0，已直接记为已结款",
  imported_settlement_paid: "审核通过。已有导入的已付款结款单，转待传结款截图",
};

/** 审核通过的成功提示：按接口的 review_outcome 出（置换单直接已结款，按结款状态推会误报）。 */
export function reviewOutcomeText(outcome: string | null | undefined): string {
  return outcome && Object.prototype.hasOwnProperty.call(OUTCOME_TEXT, outcome)
    ? OUTCOME_TEXT[outcome as ReviewOutcome]
    : "审核通过";
}

/**
 * 矩阵的 review 是一个动作（通过 + 驳回，★¹⁵ in_dialog）：菜单里拆成「审核通过」「审核驳回」两项。
 * - 可点但带 missing（缺收款码 / 寄回单号）：「审核通过」禁用并写「缺：…」，「审核驳回」照常可点
 * - 读（PR「需 PR 主管审核」）/ 灰：两项都禁用，原因跟着 label
 * buildMenuItems 已按 handler 的 label（「审核通过」）生成了 key = review 的那一项，这里就地替换。
 */
export function expandReviewItem(
  items: FlowMenuItem[],
  action: UiAction | undefined,
  onReject: () => void,
): FlowMenuItem[] {
  const index = items.findIndex((it) => it.key === "review");
  if (index < 0 || !action) return items;
  const approve = items[index];
  const enabled = action.state === "enabled";
  const missing = action.missing ?? [];
  const approveItem: FlowMenuItem =
    enabled && missing.length
      ? {
          key: "review",
          label: `审核通过（缺：${missing.map((m) => m.label).join("、")}）`,
          disabled: true,
        }
      : approve;
  const rejectItem: FlowMenuItem = {
    key: "review-reject",
    label: enabled ? "审核驳回" : approve.label.replace("审核通过", "审核驳回"),
    disabled: !enabled,
    danger: true,
    onClick: enabled ? onReject : undefined,
  };
  return [...items.slice(0, index), approveItem, rejectItem, ...items.slice(index + 1)];
}

/** 复盘列的 7 天数据角标（badges）：可以录了 → 「待录数据」；满 7 天还没结款 → 灰「满 7 天 · 待结款」。 */
export function metricsBadge(
  badges: readonly string[] | null | undefined,
): { label: string; color: string; tip: string } | null {
  if (badges?.includes("metrics_due")) {
    return { label: "待录数据", color: "blue", tip: "已结款，发布满 7 天，可以录 7 天数据" };
  }
  if (badges?.includes("metrics_wait_settle")) {
    return { label: "满 7 天 · 待结款", color: "default", tip: "发布已满 7 天，结款后才能录 7 天数据" };
  }
  return null;
}

export const QR_LOCKED_PAYING = "财务付款中，已锁定；码不对请让财务驳回后重传";
// 过渡期：召回 / 寄回运费被驳回时可换码的例外随 PR-8 才有，到时把后半句加回来。
export const QR_LOCKED_SETTLED = "已结款，收款码已锁定";
export const QR_READONLY_INACTIVE = "已停用 / 已取消，只读";

/**
 * 收款码弹窗能不能换、只读时写什么（7.3 收款码锁 ① ② ③ ①'）。能不能换只看详情 ui.fields.payment_qr 是不是 edit；
 * 只读的说明按单据当前情况挑一句：结款单待财务付款 → 付款中锁定；推广单已付款 / 已结款 → 已结款锁定；
 * 已停用 / 已取消 / 没发布 → 只读；灰（没发布）→ 接口的 hint（「发布后上传」）。
 */
export function paymentQrView(
  detail: Pick<Promotion, "ui" | "settlement" | "settlement_status" | "is_active" | "publish_status">,
): { editable: boolean; notice: string | null } {
  const field = detail.ui?.fields?.payment_qr;
  if (!field) return { editable: false, notice: null };
  if (field.state === "edit") return { editable: true, notice: null };
  if (field.state === "grey") return { editable: false, notice: field.hint ?? "发布后上传" };
  if (detail.settlement?.status === "待财务付款") return { editable: false, notice: QR_LOCKED_PAYING };
  if (["已付款", "已结款"].includes(detail.settlement_status)) {
    return { editable: false, notice: QR_LOCKED_SETTLED };
  }
  if (!detail.is_active || detail.publish_status !== "已发布") {
    return { editable: false, notice: QR_READONLY_INACTIVE };
  }
  return { editable: false, notice: null };
}

/** 结款相关弹窗里上传的图片：与后端 check_image_payload 同一套格式 / 大小限制。 */
export const SETTLEMENT_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
export const SETTLEMENT_IMAGE_MAX_BYTES = 10 * 1024 * 1024;

export function settlementImageError(label: string, file: Pick<File, "type" | "size">): string | null {
  if (!(SETTLEMENT_IMAGE_TYPES as readonly string[]).includes(file.type)) {
    return `${label}仅支持 JPG、PNG、WebP 图片`;
  }
  if (file.size === 0) return `${label}文件不能为空`;
  if (file.size > SETTLEMENT_IMAGE_MAX_BYTES) return `${label}图片不能超过 10MB`;
  return null;
}
