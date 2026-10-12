// 谈款审核、收货、复制弹窗的纯函数（流程线设计 8.3）。vitest 测。界面不显示商品编码与 SKU 编码（规-1）。

import dayjs from "dayjs";
import type { ReceiverValues } from "@/features/promotion/components/receiverForm";
import type { Negotiation, NegotiationReceiverConfirm, OthersInProgressItem } from "./types";

/** 后端 NotPrOwner 的原因：主管待审的谈款是自己建的（11-24），第一级要另一位持主管审核权的人审。 */
export const SELF_REVIEW_REASON = "不能审核自己谈的单";

export const SELF_REVIEW_NOTICE =
  "这张谈款是你自己建的，第一级要另一位有主管审核权的人审，管理员不能代做。请找管理员临时授权代理人。";

export const MANAGER_REJECT_HINT = "驳回后这张谈款结束，显示「审批未通过」；PR 可以复制为新谈款重谈";
export const FINAL_RETURN_HINT = "退回后 PR 改完重提，仍先过主管";
export const RECEIVER_CONFIRM_TEXT = "确认后生成推广单，之后改地址请到推广单上改";
export const COPY_CONFIRM_TEXT =
  "复制博主、商品、颜色尺码、模式、平台、服务费、备注和约定发布时间，生成一张新草稿";

/**
 * 提交成功后是否要提示「找管理员临时授权代理人」（11-24）：提交人自己有主管审核权、但这张是他谈的。
 * 按接口 ui 判断（主管审核按钮因「不能审核自己谈的单」而禁用），不看角色码。
 */
export function selfReviewNotice(row: Pick<Negotiation, "status" | "ui">): string | null {
  if (row.status !== "主管待审") return null;
  const action = row.ui?.actions?.manager_review;
  return action?.state === "disabled" && action.reason === SELF_REVIEW_REASON ? SELF_REVIEW_NOTICE : null;
}

/** 同一博主别的 PR 在谈（7c-16）：一条一行「王小美也在谈这个博主（主管待审，10-06 更新）」。 */
export function othersInProgressLines(items: readonly OthersInProgressItem[] | null | undefined): string[] {
  return (items ?? []).map(
    (o) => `${o.pr_name}也在谈这个博主（${o.status}，${dayjs(o.updated_at).format("MM-DD")} 更新）`
  );
}

/** 收货弹窗顶部：不是谈款 PR 本人打开时写「谈款 PR：王小美（由你代为确认）」。 */
export function receiverProxyNotice(
  row: Pick<Negotiation, "pr_id" | "pr_name">,
  currentUserId: string | null | undefined
): string | null {
  if (!currentUserId || row.pr_id === currentUserId) return null;
  return `谈款 PR：${row.pr_name ?? "—"}（由你代为确认）`;
}

export interface ReceiverPrefill {
  /** 库里已存的三项：保存时按它算改了哪几项。 */
  stored: ReceiverValues;
  /** 打开时填进表单的值：已存过就用存的，否则用 receiver_suggestion。 */
  values: ReceiverValues;
  /** 用了预填时的说明；没用为 null。 */
  note: string | null;
}

/** 收货弹窗的初值：已存过任一项就照存的；三项都空且有 receiver_suggestion 时预填并说明来源。 */
export function receiverPrefill(
  row: Pick<Negotiation, "receiver_name" | "receiver_phone" | "receiver_address" | "receiver_suggestion">
): ReceiverPrefill {
  const stored: ReceiverValues = {
    receiver_name: row.receiver_name ?? "",
    receiver_phone: row.receiver_phone ?? "",
    receiver_address: row.receiver_address ?? "",
  };
  const hasStored = Object.values(stored).some((v) => v.trim());
  const s = row.receiver_suggestion;
  if (hasStored || !s) return { stored, values: stored, note: null };
  return {
    stored,
    values: {
      receiver_name: s.receiver_name ?? "",
      receiver_phone: s.receiver_phone ?? "",
      receiver_address: s.receiver_address ?? "",
    },
    note: `已带出 ${s.source_label} 的收货信息，请核对`,
  };
}

/** 确认收货的请求体：以表单为准，去首尾空白，空的给 null（缺项由后端 ★⁴ 一次全报）。 */
export function receiverConfirmBody(
  values: Partial<Record<keyof ReceiverValues, string | null | undefined>>
): NegotiationReceiverConfirm {
  const clean = (v: string | null | undefined) => (v ?? "").trim() || null;
  return {
    receiver_name: clean(values.receiver_name),
    receiver_phone: clean(values.receiver_phone),
    receiver_address: clean(values.receiver_address),
  };
}

/** 「去推广管理」：推广列表按内部编码搜索。 */
export function promotionSearchPath(internalCode: string): string {
  return `/promotions?keyword=${encodeURIComponent(internalCode)}`;
}
