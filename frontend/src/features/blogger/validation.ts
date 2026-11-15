import { isApiError } from "@/services/apiClient";
import type { BloggerCreate } from "./types";

const FIELD_LABELS: Partial<Record<keyof BloggerCreate, string>> = {
  xiaohongshu_id: "账号", nickname: "昵称", platform: "平台", level: "等级",
  blogger_type: "类型", gender_target: "受众性别", follower_count: "粉丝数",
  wechat: "微信", homepage_url: "主页链接", category_tags: "类目标签",
  quote_note: "报价备注", remark: "备注", claim_work_wechat_id: "认领主工作微信",
};

/** 仅博主保存表单使用后端字段详情；不显示原始 input，避免回显联系方式。 */
export function bloggerValidationErrors(err: unknown): {name: keyof BloggerCreate; errors: string[]}[] {
  if (!isApiError(err) || err.response.data.code !== "VALIDATION_ERROR") return [];
  const errors = err.response.data.details?.errors;
  if (!Array.isArray(errors)) return [];
  return errors.flatMap(error => {
    if (!error || !Array.isArray(error.loc) || error.loc[0] !== "body") return [];
    const name = error.loc[1] as keyof BloggerCreate;
    const label = FIELD_LABELS[name];
    if (!label) return [];
    let reason: string;
    if (error.type === "string_too_long" && typeof error.ctx?.max_length === "number") {
      reason = `${label}最多 ${error.ctx.max_length} 个字符`;
    } else if (error.type === "string_pattern_mismatch" && name === "xiaohongshu_id") {
      reason = "账号只能包含字母、数字、_ . -";
    } else if (name === "follower_count") {
      reason = "粉丝数必须为非负整数";
    } else if (error.type === "missing" || error.type === "string_too_short") {
      reason = `请填写${label}`;
    } else {
      reason = `${label}：${typeof error.msg === "string" ? error.msg.replace(/^Value error, /, "") : "请检查填写内容"}`;
    }
    return [{name, errors: [reason]}];
  });
}
