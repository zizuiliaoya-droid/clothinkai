import dayjs from "dayjs";
import type { Dayjs } from "dayjs";

/** 实际发布日期不能晚于今天（7a-7）。后端按北京时间再判一次，这里只是不让选。 */
export const disableFutureDate = (d: Dayjs) => d.isAfter(dayjs(), "day");

// 站外推广人工源列（对齐 final.xlsx），从 source_extra 读取
export type SourceField = {
  name: string;
  type: "text" | "select";
  options?: string[];
};
// 寄回单号 / 点赞数 / 收藏数 / 评论数 已删（7a-5）：各有 typed 字段（寄回单号走「填寄回单号」，
// 三个数走「录 7 天数据」），这里再填只进 JSONB、哪儿都不认。JSONB 里的旧值原样留档。
// 颜色及规格 / 打单地址 / 发货单号 也已删（流程线 PR-2）：分别改走商品明细、收件三项、仓库回填；
// 后端对后两个键 422 SOURCE_EXTRA_KEY_RETIRED。PR-9 起「合作形式」只作导入内容分类列，旧JSON留档。
export const SOURCE_FIELDS: SourceField[] = [
  { name: "订单号", type: "text" },
  // 「合作方式」已提成 typed 字段 cooperation_mode，不再走 source_extra —— 它决定成本
  // 口径与审核后的流转出口，必须是后端能校验的字段。
  { name: "负责PR", type: "text" },
];
export const SOURCE_FIELD_NAMES = SOURCE_FIELDS.map((f) => f.name);
/** 导入的“合作形式”指内容分类，独立于旧source_extra录入表单。 */
export const PROMOTION_IMPORT_COLUMNS = ["款式编码", "SKU编码", "小红书ID", "报价金额", "成本快照", "平台", "合作日期", "计划发布日期", "合作形式", "笔记标题", "备注"];
export const CONTENT_CATEGORIES = ["单篇", "合集"] as const;
/** 推广列表的 source_extra 只读列：旧单的「颜色及规格」原文照常显示（流程线 8.4），打单地址 / 发货单号已搬到 typed 列。 */
export const SOURCE_LIST_COLUMNS = ["颜色及规格", ...SOURCE_FIELD_NAMES];

/** 合作模式。单据生成后不可改，所以只在新建表单里出现。 */
export const COOPERATION_MODES = ["寄拍", "送拍", "置换"] as const;

export const COOPERATION_MODE_HINT: Record<string, string> = {
  寄拍: "衣服要寄回，样品成本记 0，只有寄回运费计入成本",
  送拍: "衣服送给博主，样品成本取商品成员款式的货品成本之和",
  置换: "以货换推广，没有博主服务费",
};

export const recallColor: Record<string, string> = {
  召回中: "orange",
  召回成功: "green",
  召回失败: "red",
};
