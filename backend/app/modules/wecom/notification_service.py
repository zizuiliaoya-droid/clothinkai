"""U07 站内通知服务（MVP 首个消费者 = 频控降级；V1 设计模块复用）。"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.modules.wecom.enums import NotificationType
from app.modules.wecom.models import Notification
from app.modules.wecom.repository import NotificationRepository


class NotificationService:
    def __init__(self, session) -> None:
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
            self._repo.add(
                Notification(user_id=uid, type=type, content=content, link=link)
            )
        await self._s.flush()

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Notification]:
        return await self._repo.list_for_user(
            user_id=user_id, unread_only=unread_only, limit=limit, offset=offset
        )

    async def unread_count(self, *, user_id: UUID) -> int:
        return await self._repo.unread_count(user_id=user_id)

    async def mark_read(self, *, notification_id: UUID, user_id: UUID) -> bool:
        n = await self._repo.get(notification_id)
        if n is None or n.user_id != user_id:
            return False
        n.is_read = True
        await self._s.flush()
        return True


__all__ = ["NotificationService"]
