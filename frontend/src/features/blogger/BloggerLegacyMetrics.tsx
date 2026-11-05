import { Collapse, Descriptions, Typography } from "antd";
import { DOUYIN_METRIC_FIELDS, douyinMetricText } from "./douyinMetrics";
import type { Blogger } from "./types";

// Historical field names from frontend/EXCEL-COLUMNS.md and aidlc-docs/metrics-dict.tsv.
// Keep the original “天” names for stored derived fields; do not recalculate or rename their keys.
const CRAWLER_METRIC_FIELDS = [
  "粉丝量", "3篇阅读量", "3篇点赞数", "3篇收藏数", "3篇评论数",
  "7篇阅读量", "7篇点赞数", "7篇收藏数", "7篇评论数",
  "14篇阅读量", "14篇点赞数", "14篇收藏数", "14篇评论数",
  "阅读点赞比", "收藏赞比", "近期数据涨的博主", "是否假号", "活跃粉丝数", "阅读粉丝数",
  "粉丝画像", "粉丝占比", "主要年龄", "粉丝赞藏比", "笔记数", "爆文率",
  "3天平均阅读", "7天平均阅读", "14天平均阅读", "3天平均点赞", "7天平均点赞", "14天平均点赞",
  "3天阅读涨跌", "7天阅读涨跌", "3天点赞涨跌", "7天点赞涨跌",
] as const;

function historicalValue(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return String(value);
  return "暂无数据";
}

/** Only known statistic fields are displayed; raw contacts/quotes never enter this section. */
export function BloggerLegacyMetrics({ blogger }: { blogger: Pick<Blogger, "is_suspected_fake" | "audience_profile" | "read_like_ratio" | "platform_metrics" | "crawler_metrics"> }) {
  const stats = blogger.audience_profile?.note_stats;
  const crawlerItems = CRAWLER_METRIC_FIELDS
    .filter(field => Object.prototype.hasOwnProperty.call(blogger.crawler_metrics ?? {}, field))
    .map(field => ({key: `crawler:${field}`, label: `历史 · ${field}`, children: historicalValue(blogger.crawler_metrics?.[field])}));
  return <Collapse size="small" style={{ marginBottom: 12 }} items={[{
    key: "historical",
    label: "历史第三方指标（已停止更新）",
    children: <>
      <Typography.Paragraph type="secondary">
        旧来源已停止新上传，历史值仅供参考。暂无替代来源；3 / 7 / 14 篇及增长数据不代表当前情况，作品记录不替代这些指标。
      </Typography.Paragraph>
      <Descriptions column={1} size="small" items={[
        { key: "source", label: "历史来源", children: blogger.platform_metrics?.source === "huitun_douyin" ? "灰豚抖音（历史来源）" : blogger.audience_profile || blogger.platform_metrics || crawlerItems.length > 0 ? "历史记录 / 来源待核实" : "暂无来源" },
        { key: "time", label: "采集时间", children: "时间未记录" },
        { key: "flag", label: "疑似标记", children: blogger.is_suspected_fake ? "疑似 · 历史记录 / 依据待核实" : "未标记（不代表正常）" },
        { key: "likes", label: "历史平均点赞", children: stats?.avg_likes ?? "暂无数据" },
        { key: "reads", label: "历史平均阅读", children: stats?.avg_reads ?? "暂无数据" },
        { key: "ratio", label: "历史点赞 / 阅读比", children: blogger.read_like_ratio ?? "暂无判定依据" },
        ...crawlerItems,
        ...DOUYIN_METRIC_FIELDS.map(field => ({key: field, label: `历史 · ${field}`, children: douyinMetricText(blogger.platform_metrics, field)})).filter(item => item.children !== "—"),
      ]} />
    </>,
  }]} />;
}
