import { Tag } from "antd";
import type { CommentState, FollowUpState } from "./engagement";

export function EngagementTags({ comment, followUp }: {
  comment?: CommentState | null; followUp?: FollowUpState | null;
}) {
  return <>
    {!!comment?.unread_comment_count && <Tag color="blue">{comment.unread_comment_count}条未读留言</Tag>}
    {followUp?.is_due && <Tag color="orange">待跟进</Tag>}
  </>;
}
