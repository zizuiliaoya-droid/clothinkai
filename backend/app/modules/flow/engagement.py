# ruff: noqa: S608 -- SQL片段仅来自代码常量，所有用户值均使用绑定参数。
"""本人留言阅读与待跟进的共用查询；不维护第二套业务状态。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.field_permissions import can_read_field
from app.modules.auth.models import User
from app.modules.flow.exceptions import LineNotFoundError
from app.modules.flow.matrix import FlowActor
from app.modules.flow.models import LineCommentRead, LineEvent
from app.modules.flow.schemas import CommentState
from app.modules.wecom.models import Notification

# 只列确实由人执行的动作；导入、通用编辑、自动开关任务和派生完结不重置计时。
HUMAN_FOLLOW_UP_EVENTS = (
    "line.comment",
    "urge.sent",
    "urge.completed",
    "negotiation.submitted",
    "negotiation.manager_approved",
    "negotiation.manager_rejected",
    "negotiation.final_approved",
    "negotiation.final_returned",
    "negotiation.receiver_saved",
    "negotiation.finalized",
    "promotion.published",
    "promotion.return_waybill_set",
    "promotion.metrics_recorded",
    "ship.pushed",
    "ship.filled",
    "ship.waybill_changed",
    "recall.started",
    "recall.waybill_uploaded",
    "recall.succeeded",
    "recall.failed",
    "review.approved",
    "review.rejected",
    "review.resubmitted",
    "settlement.payment_filled",
    "settlement.paid",
    "settlement.rejected",
    "settlement.resubmitted",
    "freight.submitted",
    "freight.approved",
    "freight.rejected",
    "freight.resubmitted",
    "freight.paid",
    "retro.written",
    "retro.edited",
)


def engagement_params(actor: FlowActor | None, *, now: datetime | None = None) -> dict[str, Any]:
    return {
        "engagement_viewer": actor.user_id if actor else None,
        "comment_visible": bool(actor and can_read_field("line_event", "comment", actor.field_ctx)),
        "engagement_see_all": bool(actor and actor.perms.has("negotiation_all", "read")),
        "engagement_now": now or datetime.now(UTC),
        "human_events": list(HUMAN_FOLLOW_UP_EVENTS),
    }


def engagement_joins(alias: str) -> str:
    """alias 是代码中的固定表别名，绝不接收用户输入。"""
    return f"""
    LEFT JOIN line_comment_read lr ON lr.tenant_id={alias}.tenant_id
      AND lr.line_id={alias}.line_id AND lr.user_id=CAST(:engagement_viewer AS uuid)
    LEFT JOIN LATERAL (
      SELECT MAX(e.created_at) FILTER (WHERE e.event_type='line.comment'
               AND :comment_visible AND (e.negotiation_id IS NULL OR EXISTS (
                 SELECT 1 FROM negotiation en WHERE en.id=e.negotiation_id
                  AND en.tenant_id=e.tenant_id AND (en.pr_id=CAST(:engagement_viewer AS uuid)
                    OR (:engagement_see_all AND en.status<>'草稿'))))) AS last_comment_at,
        COUNT(*) FILTER (WHERE e.event_type='line.comment' AND :comment_visible
          AND e.seq>COALESCE(lr.last_read_comment_seq,0)
          AND e.actor_id IS DISTINCT FROM CAST(:engagement_viewer AS uuid)
          AND (e.negotiation_id IS NULL OR EXISTS (
            SELECT 1 FROM negotiation en WHERE en.id=e.negotiation_id AND en.tenant_id=e.tenant_id
              AND (en.pr_id=CAST(:engagement_viewer AS uuid)
                OR (:engagement_see_all AND en.status<>'草稿'))))) AS unread_comment_count,
        MAX(e.created_at) FILTER (WHERE e.actor_id IS NOT NULL
          AND e.event_type=ANY(CAST(:human_events AS text[]))) AS last_human_at
      FROM line_event e WHERE e.tenant_id={alias}.tenant_id AND e.line_id={alias}.line_id
    ) activity ON true
    """


COMMENT_STATE_SQL = """CASE WHEN :comment_visible THEN jsonb_build_object(
  'last_comment_at',activity.last_comment_at,
  'last_read_comment_seq',COALESCE(lr.last_read_comment_seq,0),
  'unread_comment_count',activity.unread_comment_count) ELSE NULL END"""


def due_at_sql(alias: str) -> str:
    return f"""GREATEST(COALESCE(activity.last_human_at,{alias}.created_at)+INTERVAL '120 hours',
      ({alias}.scheduled_publish_date::timestamp AT TIME ZONE 'Asia/Shanghai'))"""


def open_obligations_sql(alias: str) -> str:
    # ship_status/return_waybill合法可空；完整谓词归一化，响应始终得到bool。
    return f"""(({alias}.recall_status='召回中'
      OR ({alias}.publish_status='已取消' AND {alias}.ship_status='已发货'
          AND {alias}.recall_status<>'召回成功')
      OR ({alias}.cooperation_mode='寄拍' AND {alias}.ship_status='已发货'
          AND NULLIF(BTRIM({alias}.return_waybill),'') IS NULL
          AND {alias}.recall_status<>'召回成功')
      OR EXISTS (SELECT 1 FROM settlement os WHERE os.tenant_id={alias}.tenant_id
          AND os.promotion_id={alias}.id AND os.settlement_status<>'已付款')
      OR EXISTS (SELECT 1 FROM return_freight_payment ofr WHERE ofr.tenant_id={alias}.tenant_id
          AND ofr.promotion_id={alias}.id AND ofr.status<>'已付款')) IS TRUE)"""


def follow_up_open_sql(alias: str, kind: str) -> str:
    if kind == "negotiation":
        # REJECTED 的矩阵仅允许 copy/comment；FINAL_RETURNED 仍能 edit/submit，必须保留。
        return f"""({alias}.status NOT IN ('审核驳回','已定稿') AND {alias}.promotion_id IS NULL)"""
    return f"""({alias}.publish_status<>'已删除' AND (
      ({alias}.is_active AND {alias}.completed_at IS NULL AND {alias}.publish_status<>'已取消')
      OR {open_obligations_sql(alias)}))"""


def needs_follow_up_sql(alias: str, kind: str) -> str:
    return f"({follow_up_open_sql(alias, kind)} AND {due_at_sql(alias)}<=:engagement_now)"


def follow_up_state_sql(alias: str, kind: str) -> str:
    return f"""jsonb_build_object('last_human_follow_up_at',
      COALESCE(activity.last_human_at,{alias}.created_at),'due_at',{due_at_sql(alias)},
      'is_due',{needs_follow_up_sql(alias, kind)})"""


class LineEngagementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def state(self, line_id: UUID, user: User, actor: FlowActor) -> CommentState | None:
        if not can_read_field("line_event", "comment", actor.field_ctx):
            return None
        stmt = text(f"""SELECT {COMMENT_STATE_SQL} FROM
          (SELECT CAST(:tenant_id AS uuid) tenant_id, CAST(:line_id AS uuid) line_id) l
          {engagement_joins('l')}""")
        value = (
            await self.session.execute(
                stmt,
                {
                    **engagement_params(actor),
                    "tenant_id": user.tenant_id,
                    "line_id": line_id,
                },
            )
        ).scalar_one()
        return CommentState.model_validate(value)

    async def mark_read(self, line_id: UUID, user: User, seen_seq: int) -> CommentState:
        from app.modules.flow.timeline import LineEventService

        actor, _, sees_negotiation, _ = await LineEventService(self.session)._visible_line(
            line_id, user
        )
        event = (
            await self.session.execute(
                select(LineEvent).where(
                    LineEvent.tenant_id == user.tenant_id,
                    LineEvent.line_id == line_id,
                    LineEvent.seq == seen_seq,
                    LineEvent.event_type == "line.comment",
                )
            )
        ).scalar_one_or_none()
        if (
            not can_read_field("line_event", "comment", actor.field_ctx)
            or event is None
            or (event.negotiation_id is not None and not sees_negotiation)
        ):
            raise LineNotFoundError()
        stmt = insert(LineCommentRead).values(
            tenant_id=user.tenant_id,
            user_id=user.id,
            line_id=line_id,
            last_read_comment_seq=seen_seq,
        )
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=["tenant_id", "user_id", "line_id"],
                set_={
                    "last_read_comment_seq": text(
                        "GREATEST(line_comment_read.last_read_comment_seq,EXCLUDED.last_read_comment_seq)"
                    )
                },
            )
        )
        await self.session.execute(
            update(Notification)
            .where(
                Notification.tenant_id == user.tenant_id,
                Notification.user_id == user.id,
                Notification.type == "line_comment",
                Notification.line_id == line_id,
                Notification.event_seq <= seen_seq,
            )
            .values(is_read=True)
        )
        await self.session.commit()
        state = await self.state(line_id, user, actor)
        assert state is not None
        return state

    async def notify_comment(self, event: LineEvent, author: User) -> None:
        from app.modules.flow.timeline import LineEventService

        users = (
            (
                await self.session.execute(
                    select(User).where(
                        User.tenant_id == author.tenant_id,
                        User.status == "active",
                        User.deleted_at.is_(None),
                        User.id != author.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        for user in users:
            try:
                actor, header, sees_neg, sees_promo = await LineEventService(
                    self.session
                )._visible_line(event.line_id, user)
            except LineNotFoundError:
                continue
            if "platform_admin" in actor.field_ctx.role_codes:
                continue
            if not can_read_field("line_event", "comment", actor.field_ctx):
                continue
            if event.negotiation_id is not None and not sees_neg:
                continue
            owners = {
                obj.pr_id for obj in (header.negotiation, header.promotion) if obj is not None
            }
            if user.id not in owners and not actor.perms.has("negotiation_all", "read"):
                continue
            if not actor.perms.has("notification", "read"):
                continue
            if header.promotion is not None and sees_promo and actor.perms.has("promotion", "read"):
                link = f"/promotions?id={header.promotion.id}"
            elif header.negotiation is not None and sees_neg:
                link = f"/negotiations?id={header.negotiation.id}"
            else:
                continue
            self.session.add(
                Notification(
                    tenant_id=author.tenant_id,
                    user_id=user.id,
                    type="line_comment",
                    content="有新留言",
                    link=link,
                    line_id=event.line_id,
                    event_seq=event.seq,
                )
            )
        await self.session.flush()
