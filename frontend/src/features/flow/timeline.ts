// 主线时间线：响应类型、事件文案、「改了什么」的展开文字、留言校验（流程线设计 6.5、6.6、8.1）。纯函数，vitest 测。
//
// 事件类型照抄后端 flow/line_events.py 的 LineEventType；timeline.test.ts 拿 flow_keys.snapshot.json 的 EVENT_GROUP
// 逐项比，后端加了类型这边没文案就红。
// payload 已由后端按 PAYLOAD_FIELD_RULES 裁剪（看不到的键不会来）；这里再做一道显示白名单：
// 不认识的键、存 id 的键都不显示原值（规-1：页面不出现商品编码 / SKU 编码，id 也没有可读意义）。
import type { UiState } from "./keys";

export interface TimelineActor {
  id: string;
  display_name: string;
}

export interface TimelineAttachment {
  id: string;
  /** 私有桶签名（900 秒）；签名失败为 null。 */
  url: string | null;
  mime: string;
}

export interface TimelineEvent {
  seq: number;
  event_type: string;
  /** 分组中文名：谈款 / 留言 / 推广 / 发货 / 结款 / 复盘。 */
  group: string;
  created_at: string;
  /** null = 系统（扫描、导入）。 */
  actor: TimelineActor | null;
  from_state: string | null;
  to_state: string | null;
  note: string | null;
  attachment: TimelineAttachment | null;
  payload: Record<string, unknown>;
}

export interface HiddenSegment {
  group: string;
  count: number;
  reason: string;
}

export interface LegacyPoint {
  at: string;
  text: string;
  source: string;
}

export interface LineTimelineData {
  line_id: string;
  negotiation: { id: string; status: string } | null;
  promotion: { id: string; internal_code: string; stage: string } | null;
  next_step: { text: string; who: string } | null;
  /** 最新在上（seq 倒序）。 */
  events: TimelineEvent[];
  hidden_segments: HiddenSegment[];
  legacy: LegacyPoint[];
  truncated: boolean;
  /** 只有 ui.actions.comment：留言框出不出现。 */
  ui: Pick<UiState, "actions">;
}

/** legacy 段的整段标注（6.5）；后端每条文案不带后缀，由这里统一标。 */
export const LEGACY_LABEL = "上线前记录（按单据字段还原，可能不全）";
/** 留言框下的提示（11-21：留言里写地址只提示、不识别）。 */
export const COMMENT_RECEIVER_HINT = "收货信息请填在收货栏，不要写在留言里";
export const COMMENT_EMPTY_TEXT = "请写留言或贴一张图";
export const COMMENT_MAX_LENGTH = 2000;
/** 与后端 check_image_payload 同一套限制。 */
export const COMMENT_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"] as const;
export const COMMENT_IMAGE_MAX_BYTES = 10 * 1024 * 1024;
export const TRUNCATED_TEXT = "记录太多，只显示最近 500 条";

export const EVENT_LABELS: Record<string, string> = {
  // 谈款
  "negotiation.created": "新建谈款",
  "negotiation.copied": "从旧谈款复制新建",
  "negotiation.copied_out": "复制出新谈款",
  "negotiation.submitted": "提交谈款审核",
  "negotiation.manager_approved": "主管审核通过谈款",
  "negotiation.manager_rejected": "主管驳回谈款",
  "negotiation.final_approved": "老板审核通过谈款",
  "negotiation.final_returned": "老板退回谈款",
  "negotiation.receiver_saved": "保存收货信息",
  "negotiation.finalized": "谈款定稿，生成推广单",
  "negotiation.reassigned": "转交谈款",
  // 留言
  "line.comment": "留言",
  // 推广
  "promotion.created": "新建推广单",
  "promotion.imported": "导入推广单",
  "promotion.updated": "修改推广单",
  "promotion.published": "登记发布",
  "promotion.cancelled": "取消推广",
  "promotion.brand_comment_uploaded": "上传品牌词评论截图",
  "promotion.return_waybill_set": "填写寄回单号",
  "promotion.metrics_recorded": "录入 7 天数据",
  "urge.task_opened": "开始催发",
  "urge.sent": "催发博主",
  "urge.completed": "催发标记完成",
  "urge.reopened": "重新开始催发",
  "urge.closed": "结束催发",
  "recall.started": "发起召回",
  "recall.waybill_uploaded": "上传召回寄回单号",
  "recall.succeeded": "召回成功",
  "recall.failed": "召回失败",
  "promotion.completed": "推广完结",
  // 发货
  "ship.included": "纳入发货",
  "ship.pushed": "推送仓库发货",
  "ship.withdrawn": "从仓库撤回",
  "ship.filled": "仓库回填快递单号",
  "ship.waybill_changed": "修改快递信息",
  "ship.receiver_changed": "修改收货信息",
  "ship.items_changed": "修改发货明细",
  // 结款
  "promotion.payment_qr_uploaded": "上传收款码",
  "review.approved": "主管审核通过推广",
  "review.rejected": "主管驳回推广",
  "review.resubmitted": "重提推广审核",
  "settlement.created": "生成结款单",
  "settlement.extra_item_added": "追加结款项",
  "settlement.payment_filled": "填写付款信息",
  "settlement.paid": "财务已付款",
  "settlement.rejected": "驳回结款",
  "settlement.resubmitted": "重提结款",
  "settlement.legacy_approved": "旧单结款审核通过",
  "promotion.settled": "推广已结款",
  "freight.submitted": "提交运费报销",
  "freight.approved": "运费审核通过",
  "freight.rejected": "驳回运费",
  "freight.paid": "运费已付款",
  "freight.resubmitted": "重提运费",
  // 复盘
  "retro.written": "提交复盘",
  "retro.edited": "修改复盘",
  "retro.confirmed": "主管确认复盘",
  "retro.returned": "主管打回复盘",
};

/** 不认识的类型（后端新加、前端还没跟上）显示通用文案，不显示类型码。 */
export function eventLabel(eventType: string): string {
  return EVENT_LABELS[eventType] ?? "其他记录";
}

/** 附件的 alt（8.9：图片都有 alt）。 */
const ATTACHMENT_ALT: Record<string, string> = {
  "line.comment": "留言贴图",
  "promotion.payment_qr_uploaded": "收款码",
  "promotion.brand_comment_uploaded": "品牌词评论截图",
  "promotion.published": "品牌词评论截图",
  "promotion.metrics_recorded": "7 天数据截图",
  "urge.sent": "催发截图",
  "recall.waybill_uploaded": "寄回单号截图",
};

export function attachmentAlt(eventType: string): string {
  return ATTACHMENT_ALT[eventType] ?? `${eventLabel(eventType)}附件`;
}

// ---------------------------------------------------------------------------
// 「改了什么」
// ---------------------------------------------------------------------------

/** 显示白名单：键 → 中文名。不在表里的键不显示。 */
const KEY_LABELS: Record<string, string> = {
  quote_amount: "博主服务费",
  payable: "应付金额",
  freight_amount: "运费付款金额",
  return_shipping_fee: "寄回运费",
  return_shipping_payer: "承担方",
  receiver_name: "收件人",
  receiver_phone: "收件电话",
  receiver_address: "收货地址",
  blogger_id: "博主",
  style_id: "主款",
  goods_main_id: "归属商品",
  goods_items: "颜色尺码",
  sku_id: "主款颜色尺码",
  cooperation_mode: "合作模式",
  platform: "平台",
  scheduled_publish_date: "约定发布时间",
  from_pr_name: "原 PR",
  to_pr_name: "新 PR",
  source: "来源",
  row_number: "导入行号",
  system: "系统",
  note_title: "笔记标题",
  remark: "备注",
  is_active: "启用",
  source_extra: "其他信息",
  amount_fields: "改了金额",
  changed_fields: "改了（内容过长，未记前后值）",
  publish_url: "发布链接",
  actual_publish_date: "实际发布日期",
  by_cancel: "因取消推广撤回",
  like_count: "点赞数",
  collect_count: "收藏数",
  comment_count: "评论数",
  return_waybill: "寄回单号",
  trigger: "触发方式",
  urge_count: "第几次催发",
  close_reason: "关闭原因",
  destination: "审核后去向",
  reject_category: "驳回分类",
  items: "发货明细",
  items_before: "改前明细",
  items_after: "改后明细",
  reason: "原因",
  courier: "快递公司",
  waybill: "快递单号",
  shipped_at: "发货时间",
  ship_courier: "快递公司",
  ship_waybill: "快递单号",
};

/** 存 id 的键：只说「改了」，不显示值。 */
const ID_KEYS = new Set(["blogger_id", "style_id", "goods_main_id", "goods_items", "sku_id", "source_extra"]);
const MONEY_KEYS = new Set(["quote_amount", "payable", "freight_amount", "return_shipping_fee"]);
const ITEMS_KEYS = new Set(["items", "items_before", "items_after"]);
const TIME_KEYS = new Set(["shipped_at"]);
const SYSTEM_LABELS: Record<string, string> = { urge_scan: "自动催发扫描", import: "导入" };
const AMOUNT_FIELD_LABELS: Record<string, string> = {
  quote_amount: "博主服务费",
  cost_snapshot: "样品成本",
  return_shipping_fee: "寄回运费",
};
/** 容器键：内层另外展开，顶层不单列。 */
const SKIP_TOP = new Set(["snapshot", "diff"]);

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** 本地时间 YYYY-MM-DD HH:mm。 */
export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** 明细 [[款式简称, 颜色, 尺码]] → 「上衣 黑色 / M」，多件用「；」连。 */
export function formatItems(value: unknown): string {
  if (!Array.isArray(value) || value.length === 0) return "无";
  return value
    .map((row) => {
      if (!Array.isArray(row)) return "";
      const [name, color, size] = row.map((x) => (x == null ? "" : String(x)));
      const spec = [color, size].filter(Boolean).join(" / ");
      return [name, spec].filter(Boolean).join(" ");
    })
    .filter(Boolean)
    .join("；");
}

function formatValue(key: string, value: unknown): string {
  if (value == null || value === "") return "空";
  if (ITEMS_KEYS.has(key)) return formatItems(value);
  if (MONEY_KEYS.has(key)) {
    const n = Number(value);
    return Number.isFinite(n) ? `¥${n.toFixed(2)}` : String(value);
  }
  if (TIME_KEYS.has(key) && typeof value === "string") return formatTime(value);
  if (key === "system" && typeof value === "string") return SYSTEM_LABELS[value] ?? value;
  if (key === "amount_fields" && Array.isArray(value)) {
    return value.map((f) => AMOUNT_FIELD_LABELS[String(f)] ?? "金额").join("、");
  }
  if (key === "changed_fields" && Array.isArray(value)) {
    return value.map((f) => KEY_LABELS[String(f)] ?? "其他字段").join("、");
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "已修改";
}

const TRUNCATED_MARK = "（已截断）";

function isChange(v: unknown): v is { before: unknown; after: unknown; truncated?: unknown } {
  return typeof v === "object" && v !== null && "before" in v && "after" in v;
}

/**
 * payload → 「改了什么」的逐行文字。diff 条目「名：前 → 后」，其余「名：值」；
 * 不在显示白名单里的键丢掉，id 类只说「已更换」。没有可显示的内容返回空数组（节点不出展开）。
 */
export function describeDiff(payload: Record<string, unknown> | null | undefined): string[] {
  if (!payload) return [];
  const lines: string[] = [];
  const diff = payload.diff;
  if (diff && typeof diff === "object") {
    for (const [key, change] of Object.entries(diff as Record<string, unknown>)) {
      const label = KEY_LABELS[key];
      if (!label || !isChange(change)) continue;
      if (ID_KEYS.has(key)) {
        lines.push(`${label}：已更换`);
        continue;
      }
      // 后端把超长文本截到 200 字，截过的一侧在 truncated 里
      const cut = new Set(Array.isArray(change.truncated) ? change.truncated : []);
      const side = (s: "before" | "after") => `${formatValue(key, change[s])}${cut.has(s) ? TRUNCATED_MARK : ""}`;
      lines.push(`${label}：${side("before")} → ${side("after")}`);
    }
  }
  for (const [key, value] of Object.entries(payload)) {
    if (SKIP_TOP.has(key)) continue;
    const label = KEY_LABELS[key];
    if (!label || ID_KEYS.has(key)) continue;
    lines.push(`${label}：${formatValue(key, value)}`);
  }
  // 提交快照：第一次提交没有 diff，列出提交时的值（id 类不列）
  const snapshot = payload.snapshot;
  if (!diff && snapshot && typeof snapshot === "object") {
    for (const [key, value] of Object.entries(snapshot as Record<string, unknown>)) {
      const label = KEY_LABELS[key];
      if (!label || ID_KEYS.has(key)) continue;
      lines.push(`${label}：${formatValue(key, value)}`);
    }
  }
  return lines;
}

// ---------------------------------------------------------------------------
// 留言校验（与后端同口径：文字去首尾空白后与图都空 → 拦；图片类型 / 大小在选文件时拦）
// ---------------------------------------------------------------------------

export function commentError(text: string, image: File | null): string | null {
  if (!text.trim() && !image) return COMMENT_EMPTY_TEXT;
  if (text.length > COMMENT_MAX_LENGTH) return `留言不能超过 ${COMMENT_MAX_LENGTH} 字`;
  return null;
}

export function commentImageError(file: Pick<File, "type" | "size">): string | null {
  if (!(COMMENT_IMAGE_TYPES as readonly string[]).includes(file.type)) return "只支持 JPG、PNG、WebP 图片";
  if (file.size > COMMENT_IMAGE_MAX_BYTES) return "图片不能超过 10MB";
  return null;
}
