// 谈款新建 / 编辑弹窗的纯函数（流程线设计 8.3）：成员行、请求体、换商品重建、提交前自查、missing → 字段。vitest 测。
// 表单值形如 { blogger_id, style_id, goods_main_id, cooperation_mode, platform, scheduled_publish_date: Dayjs,
// quote_amount: number, remark, items: { [style_id]: sku_id } }；明细一行一个成员款式（同推广单 goodsItemsForm）。
// 保存草稿不卡缺项，提交卡；缺项的键与后端 SUBMIT_GATES / 422 FLOW_GATE_MISSING 的 missing 相同。

import dayjs, { type Dayjs } from "dayjs";
import type { GateMissingItem } from "@/features/flow/keys";
import {
  missingToFields,
  type FormFieldName,
  type MissingToFieldsResult,
} from "@/features/flow/missingToFields";
import { itemFieldName, type ItemValues } from "@/features/promotion/components/goodsItemsForm";
import type { GoodsMember } from "@/features/promotion/types";
import type {
  CooperationMode,
  Negotiation,
  NegotiationCreate,
  NegotiationItemIn,
} from "./types";

export interface NegotiationFormValues {
  blogger_id?: string | null;
  style_id?: string | null;
  goods_main_id?: string | null;
  cooperation_mode?: CooperationMode | null;
  platform?: string | null;
  scheduled_publish_date?: Dayjs | null;
  quote_amount?: number | null;
  remark?: string | null;
  items?: ItemValues;
}

/** 提交卡点（后端 SUBMIT_GATES，键 = 表单字段名，文案相同）。服务费不在前端查：不填取博主档案报价。 */
export const SUBMIT_GATE_LABEL = {
  blogger_id: "博主",
  style_id: "商品",
  goods_items: "颜色尺码",
  cooperation_mode: "合作模式",
  platform: "平台",
  scheduled_publish_date: "约定发布时间",
  quote_amount: "服务费",
} as const;

export const GOODS_REBUILT_HINT = "已按新商品重建颜色尺码，请重新选择";

/** 已有谈款的成员行 = 明细的款式（按明细顺序）。存量草稿没有明细时为空，弹窗再按款式调 goods-members。 */
export function membersFromItems(row: Pick<Negotiation, "items">): GoodsMember[] {
  return (row.items ?? []).map((i) => ({
    style_id: i.style_id,
    display_short_name: i.display_short_name,
    goods_title: i.goods_title,
  }));
}

/** 编辑弹窗的初值。 */
export function negotiationFormInitial(
  row: Pick<
    Negotiation,
    | "blogger_id"
    | "style_id"
    | "goods_main_id"
    | "cooperation_mode"
    | "platform"
    | "scheduled_publish_date"
    | "quote_amount"
    | "remark"
    | "items"
  >
): NegotiationFormValues {
  const items: ItemValues = {};
  for (const i of row.items ?? []) {
    if (i.sku_id) items[i.style_id] = i.sku_id;
  }
  return {
    blogger_id: row.blogger_id,
    style_id: row.style_id,
    goods_main_id: row.goods_main_id,
    cooperation_mode: row.cooperation_mode,
    platform: row.platform,
    scheduled_publish_date: row.scheduled_publish_date ? dayjs(row.scheduled_publish_date) : null,
    quote_amount: row.quote_amount != null ? Number(row.quote_amount) : null,
    remark: row.remark,
    items,
  };
}

/** 按成员顺序组明细：每个成员一行，没选的 sku 给 null（草稿可空）。 */
export function negotiationItemsPayload(
  members: readonly GoodsMember[],
  values: ItemValues | undefined
): NegotiationItemIn[] {
  return members.map((m) => ({ style_id: m.style_id, sku_id: values?.[m.style_id] || null }));
}

/**
 * 新建 / 编辑共用的请求体（NegotiationUpdate 是它的子集）。
 * 没选商品就不传 goods_main_id（后端按款式推定并写回）；还不知道成员（没调到 goods-members）就不传 items（后端按成员建空行）。
 */
export function negotiationPayload(
  values: NegotiationFormValues,
  members: readonly GoodsMember[]
): NegotiationCreate {
  const payload: NegotiationCreate = {
    blogger_id: values.blogger_id ?? "",
    style_id: values.style_id ?? "",
    cooperation_mode: values.cooperation_mode as CooperationMode,
    platform: values.platform || undefined,
    scheduled_publish_date: values.scheduled_publish_date
      ? values.scheduled_publish_date.format("YYYY-MM-DD")
      : null,
    quote_amount: values.quote_amount != null ? String(values.quote_amount) : null,
    remark: values.remark?.trim() || null,
  };
  if (values.goods_main_id) payload.goods_main_id = values.goods_main_id;
  if (members.length) payload.items = negotiationItemsPayload(members, values.items);
  return payload;
}

function sameStyles(a: readonly GoodsMember[], b: readonly GoodsMember[]): boolean {
  if (a.length !== b.length) return false;
  const ids = new Set(a.map((m) => m.style_id));
  return b.every((m) => ids.has(m.style_id));
}

/**
 * 换款式 / 商品后拿到新成员：款式集合没变就保留已选；变了整组清空，rebuilt = true（弹窗提示 GOODS_REBUILT_HINT）。
 * 之前还没有成员（刚选第一个商品）不算重建。
 */
export function rebuildOnGoodsChange(
  prev: readonly GoodsMember[],
  next: readonly GoodsMember[],
  values: ItemValues | undefined
): { items: ItemValues; rebuilt: boolean } {
  if (sameStyles(prev, next)) return { items: { ...(values ?? {}) }, rebuilt: false };
  return { items: {}, rebuilt: prev.length > 0 };
}

/** 提交前自查（保存草稿不调）：缺的项按后端卡点的键与顺序给出，交给 missingFieldsFor 标红。 */
export function negotiationSubmitMissing(
  values: NegotiationFormValues,
  members: readonly GoodsMember[]
): GateMissingItem[] {
  const missing: GateMissingItem[] = [];
  const add = (key: keyof typeof SUBMIT_GATE_LABEL) =>
    missing.push({ key, label: SUBMIT_GATE_LABEL[key] });
  if (!values.blogger_id) add("blogger_id");
  if (!values.style_id) add("style_id");
  if (!members.length || members.some((m) => !values.items?.[m.style_id])) add("goods_items");
  if (!values.cooperation_mode) add("cooperation_mode");
  if (!values.platform) add("platform");
  if (!values.scheduled_publish_date) add("scheduled_publish_date");
  return missing;
}

const FIELD_OF: Partial<Record<string, FormFieldName>> = {
  blogger_id: "blogger_id",
  style_id: "style_id",
  cooperation_mode: "cooperation_mode",
  platform: "platform",
  scheduled_publish_date: "scheduled_publish_date",
  quote_amount: "quote_amount",
};

/**
 * missing（前端自查或后端 422）→ `form.setFields` 的字段与顶部「缺：…」。
 * 颜色尺码标在没选的成员行；每行都选了还报缺（sku 已删或成员变了）就标全部行；还没有成员行就进「缺：…」。
 */
export function missingFieldsFor(
  missing: readonly GateMissingItem[],
  members: readonly GoodsMember[],
  items: ItemValues | undefined
): MissingToFieldsResult {
  const other = missing.filter((m) => m.key !== "goods_items");
  const result = missingToFields(other, FIELD_OF);
  const goods = missing.find((m) => m.key === "goods_items");
  if (!goods) return result;
  if (!members.length) {
    const rest = result.rest ? `${result.rest}、${goods.label}` : `缺：${goods.label}`;
    return { fields: result.fields, rest };
  }
  const empty = members.filter((m) => !items?.[m.style_id]);
  const rows = empty.length ? empty : members;
  return {
    fields: [
      ...result.fields,
      ...rows.map((m) => ({ name: itemFieldName(m.style_id), errors: ["必填"] })),
    ],
    rest: result.rest,
  };
}
