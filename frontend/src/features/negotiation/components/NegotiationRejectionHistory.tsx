import { Alert, Space, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { getLineEvents, lineEventsKey } from "@/features/flow/api";
import { formatTime } from "@/features/flow/timeline";
import type { Negotiation } from "../types";

/** Reuse the original line's audit trail so later submissions cannot hide old rejections. */
export function NegotiationRejectionHistory({ row }: { row: Negotiation }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: lineEventsKey(row.line_id),
    queryFn: () => getLineEvents(row.line_id),
  });
  const events = data?.events.filter(event => event.event_type === "negotiation.manager_rejected" && event.note) ?? [];
  const currentReason = (row.status === "审核驳回" || row.status === "已取消") ? row.review_opinion : null;

  if (isLoading && !currentReason) return <Typography.Text type="secondary">正在读取审核记录…</Typography.Text>;
  if (!events.length && !currentReason && !isError) return null;

  return (
    <Alert
      type="warning"
      showIcon
      style={{ marginTop: 16 }}
      message="历史驳回原因"
      description={
        <Space direction="vertical" size={8} style={{ width: "100%" }}>
          {events.map(event => (
            <div key={event.seq}>
              <Typography.Text type="secondary">{formatTime(event.created_at)} · {event.actor?.display_name ?? "主管"}</Typography.Text>
              <div style={{ whiteSpace: "pre-wrap" }}>{event.note}</div>
            </div>
          ))}
          {!events.length && currentReason && <div>
            <Typography.Text type="secondary">{formatTime(row.reviewed_at ?? row.updated_at)} · {row.reviewer_name ?? "主管"}</Typography.Text>
            <div style={{ whiteSpace: "pre-wrap" }}>{currentReason}</div>
          </div>}
          {isError && <Typography.Text type="secondary">历史审核记录暂时无法读取，请重新打开后重试。</Typography.Text>}
          {data?.truncated && <Typography.Text type="secondary">当前只展示最近记录中的驳回原因。</Typography.Text>}
        </Space>
      }
    />
  );
}
