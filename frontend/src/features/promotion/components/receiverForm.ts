// 收件三项表单的共用部分（ReceiverModal、ShipPushModal）。纯函数，vitest 测。
// 后端规则：传了才改，去首尾空白后空串 = 清空；电话由后端规范化（去空格与 -、+86），不合格 422 INVALID_RECEIVER_PHONE。

import type { Promotion } from "@/features/promotion/types";

export const RECEIVER_FIELDS = ["receiver_name", "receiver_phone", "receiver_address"] as const;
export type ReceiverField = (typeof RECEIVER_FIELDS)[number];
export type ReceiverValues = Record<ReceiverField, string>;

/** 与后端 schema 的 max_length 一致。 */
export const RECEIVER_MAX_LEN: Record<ReceiverField, number> = {
  receiver_name: 32,
  receiver_phone: 32,
  receiver_address: 255,
};

export const RECEIVER_LABEL: Record<ReceiverField, string> = {
  receiver_name: "收件人",
  receiver_phone: "收件电话",
  receiver_address: "收件地址",
};

export function receiverInitial(row: Pick<Promotion, ReceiverField>): ReceiverValues {
  return {
    receiver_name: row.receiver_name ?? "",
    receiver_phone: row.receiver_phone ?? "",
    receiver_address: row.receiver_address ?? "",
  };
}

/** 只交相对打开时改过的项（去首尾空白后比）；清空的给 null。 */
export function receiverPatch(
  initial: ReceiverValues,
  values: Partial<Record<ReceiverField, string | null | undefined>>
): Partial<Record<ReceiverField, string | null>> {
  const patch: Partial<Record<ReceiverField, string | null>> = {};
  for (const f of RECEIVER_FIELDS) {
    const next = (values[f] ?? "").trim();
    if (next === initial[f].trim()) continue;
    patch[f] = next || null;
  }
  return patch;
}

/** 422 INVALID_RECEIVER_PHONE → 标在电话那一栏的错误文字；别的错误返回 null。 */
export function receiverPhoneError(err: unknown): string | null {
  const data = (err as { response?: { data?: { code?: unknown; message?: unknown } } } | null)?.response
    ?.data;
  if (data?.code !== "INVALID_RECEIVER_PHONE") return null;
  return typeof data.message === "string" && data.message
    ? data.message
    : "电话格式不对：11 位手机号，或 0 开头的座机";
}
