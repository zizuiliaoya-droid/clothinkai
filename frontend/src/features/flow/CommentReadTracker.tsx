import { useEffect, useRef, useState } from "react";
import { Button, Typography } from "antd";
import { useQueryClient } from "@tanstack/react-query";
import { markLineCommentsRead, lineEventsKey } from "./api";
import type { LineTimelineData } from "./timeline";

/** 仅由已经打开且成功展示时间线的抽屉挂载，不在GET/预取时标读。 */
export function CommentReadTracker({ lineId, data }: { lineId: string; data: LineTimelineData }) {
  const client = useQueryClient();
  const attempted = useRef(0);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const shownSeq = Math.max(0, ...data.events.filter((e) => e.event_type === "line.comment").map((e) => e.seq));
  const readSeq = data.comment_state?.last_read_comment_seq ?? 0;
  const canReadComments = data.comment_state != null;
  useEffect(() => {
    if (!canReadComments || shownSeq <= readSeq || shownSeq <= attempted.current) return;
    attempted.current = shownSeq;
    let active = true;
    markLineCommentsRead(lineId, shownSeq).then((state) => {
      if (!active) return;
      setFailed(false);
      client.setQueryData<LineTimelineData>(lineEventsKey(lineId), (current) => current ? { ...current, comment_state: state } : current);
      for (const key of ["negotiations", "promotions", "promotion-stage-counts", "notifications"]) {
        void client.invalidateQueries({ queryKey: [key] });
      }
    }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; attempted.current = 0; };
  }, [client, lineId, shownSeq, readSeq, retry, canReadComments]);
  if (!failed) return null;
  return <Typography.Text type="secondary">阅读状态暂未同步 <Button size="small" type="link" onClick={() => {
    attempted.current = 0; setRetry((n) => n + 1);
  }}>重试</Button></Typography.Text>;
}
