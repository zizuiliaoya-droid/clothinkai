// 主线时间线与留言接口（流程线设计 7.6）。
import { apiClient } from "@/services/apiClient";
import type { LineTimelineData, TimelineEvent } from "./timeline";

/** react-query 键：抽屉里做了任何动作后 invalidate 它重新拉（6.6）。 */
export function lineEventsKey(lineId: string | null | undefined) {
  return ["line-events", lineId] as const;
}

/** 一条主线的时间线；看不到这条线的任何单据 → 404 LINE_NOT_FOUND。 */
export async function getLineEvents(lineId: string): Promise<LineTimelineData> {
  const resp = await apiClient.get<LineTimelineData>(`/api/lines/${lineId}/events`);
  return resp.data;
}

/** 留言 / 贴图：文字或一张图至少一项（都空 422、伪图 422、运营 403）；返回新写的那条事件。 */
export async function postLineComment(
  lineId: string,
  body: { text?: string | null; image?: File | null }
): Promise<TimelineEvent> {
  const form = new FormData();
  const text = body.text?.trim();
  if (text) form.append("text", text);
  if (body.image) form.append("image", body.image, body.image.name);
  const resp = await apiClient.post<TimelineEvent>(`/api/lines/${lineId}/comments`, form);
  return resp.data;
}
