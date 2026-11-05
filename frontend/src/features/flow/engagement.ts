export interface CommentState {
  last_comment_at: string | null;
  last_read_comment_seq: number;
  unread_comment_count: number;
}

export interface FollowUpState {
  last_human_follow_up_at: string;
  due_at: string;
  is_due: boolean;
}
