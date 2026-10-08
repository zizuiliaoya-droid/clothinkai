// 催发任务（PRD V1.4 改动 2）类型。

export type UrgeTaskStatus = "进行中" | "已关闭";
export type UrgeCloseReason = "博主已发布" | "已取消" | "手动关闭";
export type UrgeTriggerType = "手动" | "自动";

export interface UrgeConfig {
  no_publish_days: number;
  max_urge_times: number;
  max_overdue_days: number;
  urge_threshold_days: number;
  important_threshold_days: number;
  auto_scan_enabled: boolean;
}

export interface UrgeRecord {
  id: string;
  trigger_type: UrgeTriggerType;
  note: string | null;
  /** 截图签名 URL，后端现签不落库，有效期 1 小时。 */
  screenshot_url: string | null;
  wecom_message_id: string | null;
  created_by: string | null;
  created_by_name: string | null;
  created_at: string;
}

export interface UrgeTask {
  id: string;
  promotion_id: string;
  promotion_internal_code: string | null;
  blogger_id: string;
  blogger_nickname: string | null;
  pr_id: string | null;
  pr_name: string | null;

  style_code: string | null;
  /** 建单时的款式简称快照。界面显示 display_short_name。 */
  style_name: string | null;
  /** 品名（7a-8）：商品简称，没填回落快照。 */
  display_short_name: string | null;
  /** 归属商品全称（悬停提示）；没有归属商品为 null。 */
  goods_title: string | null;
  scheduled_publish_date: string | null;
  publish_status: string | null;

  status: UrgeTaskStatus;
  urge_count: number;
  last_urged_at: string | null;
  closed_at: string | null;
  close_reason: UrgeCloseReason | null;

  /** 催发次数是否超过阈值。服务端算，前端别自己拿配置比。 */
  over_limit: boolean;
  /** 超期天数。未排期或还没到期为 null。 */
  overdue_days: number | null;

  created_at: string;
  updated_at: string;
}

export interface UrgeTaskDetail extends UrgeTask {
  /** 催发时间线，倒序。 */
  records: UrgeRecord[];
}

export interface UrgeTaskPage {
  items: UrgeTask[];
  total: number;
  page: number;
  page_size: number;
}

export interface UrgeTaskFilters {
  status?: UrgeTaskStatus;
  pr_id?: string;
  blogger_id?: string;
  style_id?: string;
  over_limit_only?: boolean;
  overdue_only?: boolean;
  keyword?: string;
  page?: number;
  page_size?: number;
}

export interface UrgeDashboard {
  /** 本周产生的催发次数（不是任务数 —— 同一单催三次是三次工作量）。 */
  urged_this_week: number;
  pending: number;
  overdue: number;
  over_limit: number;
  auto_scan_enabled: boolean;
  max_urge_times: number;
  week_start: string;
}

export interface UrgeBatchResult {
  style_id: string;
  urged_count: number;
  task_ids: string[];
  skipped_closed: number;
}
