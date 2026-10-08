// 流程线矩阵的键与接口 `ui` 的类型（流程线设计 7.1、8.1）。
//
// 键名是前后端契约，照抄后端 backend/app/modules/flow/matrix.py 的 ACTION_KEYS / PAGE_ACTION_KEYS / FIELD_KEYS，
// 顺序也一样（= 操作菜单的顺序）。改这里必须同步改后端。
// 接口里的键一律按 string 收：前端不认识的键直接忽略（按钮不出现，偏安全）。

export const FLOW_KINDS = ["negotiation", "promotion", "warehouse", "settlement", "freight"] as const;
export type FlowKind = (typeof FLOW_KINDS)[number];

const NEGOTIATION_ACTIONS = [
  "edit",
  "submit",
  "manager_review",
  "final_review",
  "receiver_save",
  "receiver_confirm",
  "copy",
  "comment",
] as const;

const PROMOTION_ACTIONS = [
  "ship_include",
  "ship_push",
  "ship_withdraw",
  "publish",
  "cancel",
  "recall_start",
  "recall_waybill",
  "recall_result",
  "urge",
  "urge_complete",
  "review",
  "resubmit",
  "settlement_resubmit",
  "settlement_chat",
  "metrics",
  "retro_write",
  "retro_revise",
  "freight_submit",
  "comment",
  "deactivate",
] as const;

const WAREHOUSE_ACTIONS = ["ship_fill"] as const;

const SETTLEMENT_ACTIONS = [
  "legacy_approve",
  "reject",
  "extra_item",
  "fill_payment",
  "upload_proof",
] as const;

const FREIGHT_ACTIONS = [
  "freight_review",
  "freight_pay",
  "freight_reject_payment",
  "freight_resubmit",
] as const;

const NEGOTIATION_FIELDS = [
  "basic",
  "quote_amount",
  "manager_opinion",
  "final_opinion",
  "receiver",
  "timeline",
  "overdue_badge",
  "others_in_progress",
] as const;

const PROMOTION_FIELDS = [
  "goods",
  "goods_items",
  "cooperation",
  "cooperation_mode",
  "quote_amount",
  "receiver",
  "shipping",
  "publish_info",
  "platform_posts",
  "metrics",
  "payment_qr",
  "return_waybill",
  "recall_info",
  "review_result",
  "settlement",
  "settlement_chat",
  "retro",
  "freight_payment",
  "other_info",
  "timeline",
] as const;

export type FlowActionKey =
  | (typeof NEGOTIATION_ACTIONS)[number]
  | (typeof PROMOTION_ACTIONS)[number]
  | (typeof WAREHOUSE_ACTIONS)[number]
  | (typeof SETTLEMENT_ACTIONS)[number]
  | (typeof FREIGHT_ACTIONS)[number];

/** 列表响应的页级动作（新建、导入、导出）。 */
export type FlowPageActionKey = "create" | "import" | "export";

export type FieldGroupKey = (typeof NEGOTIATION_FIELDS)[number] | (typeof PROMOTION_FIELDS)[number];

export const ACTION_KEYS: Record<FlowKind, readonly FlowActionKey[]> = {
  negotiation: NEGOTIATION_ACTIONS,
  promotion: PROMOTION_ACTIONS,
  warehouse: WAREHOUSE_ACTIONS,
  settlement: SETTLEMENT_ACTIONS,
  freight: FREIGHT_ACTIONS,
};

export const PAGE_ACTION_KEYS: Record<FlowKind, readonly FlowPageActionKey[]> = {
  negotiation: ["create"],
  promotion: ["create", "import"],
  warehouse: ["export"],
  settlement: ["import"],
  freight: [],
};

export const FIELD_KEYS: Record<FlowKind, readonly FieldGroupKey[]> = {
  negotiation: NEGOTIATION_FIELDS,
  promotion: PROMOTION_FIELDS,
  warehouse: [],
  settlement: [],
  freight: [],
};

/** 动作：enabled = 改；disabled = 读（带 reason，★ 不满足另带 missing）；grey = 灰（带 hint）。 */
export type UiActionState = "enabled" | "disabled" | "grey";
/** 字段分组：edit / read / grey；「隐」的分组不出现。 */
export type UiFieldState = "edit" | "read" | "grey";

/** ★ 卡点缺项；与 422 FLOW_GATE_MISSING 的 details.missing 逐条相同。 */
export interface GateMissingItem {
  key: string;
  label: string;
}

export interface UiAction {
  state: UiActionState;
  reason?: string;
  hint?: string;
  /** enabled + missing = 缺项能在这个动作自己的弹窗里补（in_dialog）。 */
  missing?: GateMissingItem[];
}

export interface UiField {
  state: UiFieldState;
  hint?: string;
}

/** 接口返回的 `ui`：列表行 = actions + edits；详情 / 抽屉 = actions + fields。「隐」的键不出现。 */
export interface UiState {
  column?: string;
  actions?: Partial<Record<string, UiAction>>;
  edits?: string[];
  fields?: Partial<Record<string, UiField>>;
}
