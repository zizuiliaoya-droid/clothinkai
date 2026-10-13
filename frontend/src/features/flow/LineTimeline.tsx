// 主线时间线的渲染（流程线设计 6.6、8.1）：第一项「下一步」，然后事件（最新在上）、被隐藏的分组一行说明、
// 上线前记录（整段标注）。节点图标按分组区分（antd icons，不用 emoji）；「改了什么」用原生 details 展开，不带动画。
import type { ReactNode } from "react";
import { Image, Space, Tag, Timeline, Typography, theme } from "antd";
import {
  ArrowRightOutlined,
  AuditOutlined,
  CarOutlined,
  ClockCircleOutlined,
  CommentOutlined,
  EyeInvisibleOutlined,
  FileTextOutlined,
  HistoryOutlined,
  NotificationOutlined,
  PayCircleOutlined,
} from "@ant-design/icons";
import {
  LEGACY_LABEL,
  TRUNCATED_TEXT,
  attachmentAlt,
  describeDiff,
  eventLabel,
  formatTime,
  type LineTimelineData,
  type TimelineEvent,
} from "./timeline";

const GROUP_ICONS: Record<string, ReactNode> = {
  谈款: <FileTextOutlined />,
  留言: <CommentOutlined />,
  推广: <NotificationOutlined />,
  发货: <CarOutlined />,
  结款: <PayCircleOutlined />,
  复盘: <AuditOutlined />,
};

function EventNode({ event }: { event: TimelineEvent }) {
  const { token } = theme.useToken();
  const secondary = { color: token.colorTextSecondary, fontSize: token.fontSizeSM };
  const changes = describeDiff(event.payload);
  const att = event.attachment;
  return (
    <Space direction="vertical" size={4} style={{ width: "100%" }}>
      <Space size={6} wrap>
        <Typography.Text strong>{eventLabel(event.event_type)}</Typography.Text>
        <Tag>{event.group}</Tag>
        {(event.from_state || event.to_state) && (
          <span style={secondary}>
            {event.from_state ?? "—"} → {event.to_state ?? "—"}
          </span>
        )}
      </Space>
      <span style={secondary}>
        {formatTime(event.created_at)} · {event.actor?.display_name ?? "系统"}
      </span>
      {event.note && (
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>{event.note}</Typography.Paragraph>
      )}
      {att &&
        (att.url && att.mime.startsWith("image/") ? (
          <Image
            src={att.url}
            alt={attachmentAlt(event.event_type)}
            width={96}
            height={96}
            style={{ objectFit: "cover", borderRadius: token.borderRadiusSM }}
          />
        ) : att.url ? (
          <a href={att.url} target="_blank" rel="noreferrer">
            查看{attachmentAlt(event.event_type)}
          </a>
        ) : (
          <span style={secondary}>{attachmentAlt(event.event_type)}暂时打不开，请刷新重试</span>
        ))}
      {changes.length > 0 && (
        <details>
          <summary style={{ cursor: "pointer", color: token.colorPrimary, width: "fit-content" }}>改了什么</summary>
          <ul style={{ margin: "4px 0 0", paddingInlineStart: 20 }}>
            {changes.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </details>
      )}
    </Space>
  );
}

export function LineTimeline({ data }: { data: LineTimelineData }) {
  const { token } = theme.useToken();
  const secondary = { color: token.colorTextSecondary, fontSize: token.fontSizeSM };
  const items: { key: string; dot?: ReactNode; color?: string; children: ReactNode }[] = [];

  if (data.next_step) {
    items.push({
      key: "next",
      dot: <ArrowRightOutlined />,
      color: "blue",
      children: (
        <Space size={6} wrap>
          <Typography.Text strong>下一步：{data.next_step.text}</Typography.Text>
          <Tag color="blue">{data.next_step.who}</Tag>
        </Space>
      ),
    });
  }
  for (const e of data.events) {
    items.push({
      key: `e${e.seq}`,
      dot: GROUP_ICONS[e.group] ?? <ClockCircleOutlined />,
      children: <EventNode event={e} />,
    });
  }
  for (const h of data.hidden_segments) {
    items.push({
      key: `h-${h.group}`,
      dot: <EyeInvisibleOutlined />,
      color: "gray",
      children: (
        <span style={{ color: token.colorTextSecondary }}>
          已隐藏 {h.count} 条{h.group}记录：{h.reason}
        </span>
      ),
    });
  }

  return (
    <div>
      {items.length > 0 ? (
        <Timeline items={items} />
      ) : (
        data.legacy.length === 0 && <span style={secondary}>还没有记录</span>
      )}
      {data.legacy.length > 0 && (
        <section aria-label={LEGACY_LABEL}>
          <Typography.Text strong style={{ display: "block", marginBottom: 12 }}>
            {LEGACY_LABEL}
          </Typography.Text>
          <Timeline
            items={data.legacy.map((p, i) => ({
              key: `l${i}`,
              dot: <HistoryOutlined />,
              color: "gray",
              children: (
                <Space direction="vertical" size={2}>
                  <Typography.Text>{p.text}</Typography.Text>
                  <span style={secondary}>{formatTime(p.at)}</span>
                </Space>
              ),
            }))}
          />
        </section>
      )}
      {data.truncated && <span style={secondary}>{TRUNCATED_TEXT}</span>}
    </div>
  );
}
