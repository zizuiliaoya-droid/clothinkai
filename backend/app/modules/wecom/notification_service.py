# ruff: noqa: S608 -- SQL片段仅来自代码常量，所有用户值均使用绑定参数。
"""U07 站内通知服务（MVP 首个消费者 = 频控降级；V1 设计模块复用）。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.wecom.enums import NotificationType
from app.modules.wecom.models import Notification
from app.modules.wecom.repository import NotificationRepository


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = NotificationRepository(session)

    async def notify(
        self,
        user_ids: Sequence[UUID],
        content: str,
        *,
        link: str | None = None,
        type: str = NotificationType.URGE_MANUAL.value,
    ) -> None:
        """为每个 user 写一条通知（不自行 commit，复用调用方事务）。"""
        for uid in user_ids:
            if uid is None:
                continue
            self._repo.add(Notification(user_id=uid, type=type, content=content, link=link))
        await self._s.flush()

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Notification]:
        condition, params = await self._visibility(user_id)
        stmt = select(Notification).where(Notification.user_id == user_id, text(condition))
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))
        return (
            (
                await self._s.execute(
                    stmt.order_by(Notification.created_at.desc(), Notification.id)
                    .limit(limit)
                    .offset(offset),
                    params,
                )
            )
            .scalars()
            .all()
        )

    async def unread_count(self, *, user_id: UUID) -> int:
        condition, params = await self._visibility(user_id)
        return int(
            (
                await self._s.execute(
                    text(f"""SELECT COUNT(*) FROM notification
          WHERE user_id=CAST(:notification_viewer AS uuid) AND NOT is_read AND ({condition})"""),
                    params,
                )
            ).scalar_one()
        )

    async def _visibility(self, user_id: UUID) -> tuple[str, dict[str, object]]:
        from app.core.security.field_permissions import can_read_field
        from app.modules.auth.models import User
        from app.modules.auth.repository import PermissionRepository, RoleRepository
        from app.modules.flow.actor import load_flow_actor

        user = await self._s.get(User, user_id)
        if user is None:
            return "false", {"notification_viewer": user_id}
        actor = await load_flow_actor(
            user_id, RoleRepository(self._s), PermissionRepository(self._s)
        )
        params: dict[str, object] = {
            "notification_viewer": user_id,
            "notification_tenant": user.tenant_id,
            "notice_comment": can_read_field("line_event", "comment", actor.field_ctx),
            "notice_promotion": actor.perms.has("promotion", "read"),
            "notice_negotiation": actor.perms.has("negotiation", "read"),
            "notice_all": actor.perms.has("negotiation_all", "read"),
        }
        # 只存固定文案；撤权后连该单据的留言通知及未读数也不再暴露。
        condition = """notification.tenant_id=CAST(:notification_tenant AS uuid) AND (
          (notification.type NOT IN ('line_comment','follow_up_summary'))
          OR (notification.type='follow_up_summary' AND (:notice_promotion OR :notice_negotiation))
          OR (notification.type='line_comment' AND :notice_comment AND EXISTS (
            SELECT 1 FROM line_event e
            LEFT JOIN negotiation n ON n.line_id=e.line_id AND n.tenant_id=e.tenant_id
            LEFT JOIN promotion p ON p.line_id=e.line_id AND p.tenant_id=e.tenant_id
            WHERE e.tenant_id=notification.tenant_id AND e.line_id=notification.line_id
              AND e.seq=notification.event_seq AND e.event_type='line.comment'
              AND ((p.id IS NOT NULL AND :notice_promotion)
                OR (n.id IS NOT NULL AND :notice_negotiation
                  AND (n.pr_id=CAST(:notification_viewer AS uuid) OR (:notice_all AND n.status<>'草稿'))))
              AND (e.negotiation_id IS NULL OR (n.id=e.negotiation_id AND :notice_negotiation
                  AND (n.pr_id=CAST(:notification_viewer AS uuid) OR (:notice_all AND n.status<>'草稿'))))
          )))"""
        return condition, params

    async def mark_read(self, *, notification_id: UUID, user_id: UUID) -> bool:
        n = await self._repo.get(notification_id)
        if n is None or n.user_id != user_id:
            return False
        if n.type == "line_comment":
            # 留言的阅读只能经当前已展示seq的流程线接口确认。
            return False
        n.is_read = True
        await self._s.flush()
        return True


__all__ = ["NotificationService"]
