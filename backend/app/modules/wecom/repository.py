"""U07 wecom 仓储层（5 仓储）。

RLS 自动隔离（依赖 Session 注入/SET LOCAL tenant_id）。频控当天计数用
``(created_at AT TIME ZONE 'Asia/Shanghai')::date = :today``（与 U05 daily_summary 一致）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.wecom.alert_models import WecomAlertConfig, WecomAlertLog
from app.modules.wecom.models import (
    MessageTemplate,
    Notification,
    WecomConfig,
    WecomContact,
    WecomMessage,
)


class WecomConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self) -> WecomConfig | None:
        return (
            await self._s.execute(select(WecomConfig).limit(1))
        ).scalar_one_or_none()

    def add(self, cfg: WecomConfig) -> None:
        self._s.add(cfg)


class WecomContactRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_by_blogger(self, blogger_id: UUID) -> WecomContact | None:
        stmt = select(WecomContact).where(WecomContact.blogger_id == blogger_id)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, contact: WecomContact) -> None:
        self._s.add(contact)


class MessageTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, template_type: str) -> MessageTemplate | None:
        stmt = select(MessageTemplate).where(
            MessageTemplate.template_type == template_type
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_all(self) -> dict[str, MessageTemplate]:
        rows = (await self._s.execute(select(MessageTemplate))).scalars().all()
        return {t.template_type: t for t in rows}

    def add(self, tpl: MessageTemplate) -> None:
        self._s.add(tpl)


class WecomMessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, msg: WecomMessage) -> None:
        self._s.add(msg)

    async def get(self, message_id: UUID) -> WecomMessage | None:
        return await self._s.get(WecomMessage, message_id)

    async def find_by_msgid(self, wecom_msgid: str) -> WecomMessage | None:
        stmt = select(WecomMessage).where(
            WecomMessage.wecom_msgid == wecom_msgid
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def exists_today_non_failed(
        self, *, blogger_id: UUID, pr_id: UUID | None, today: date
    ) -> bool:
        """同 (blogger, pr) 当天已有非 failed message（扫描幂等，BR-U07-34）。"""
        stmt = (
            select(func.count())
            .select_from(WecomMessage)
            .where(
                WecomMessage.blogger_id == blogger_id,
                WecomMessage.pr_id == pr_id,
                WecomMessage.status != "failed",
                text(
                    "(wecom_message.created_at AT TIME ZONE 'Asia/Shanghai')::date "
                    "= :today"
                ),
            )
        )
        n = int(
            (await self._s.execute(stmt, {"today": today})).scalar_one()
        )
        return n > 0

    async def count_today_active(
        self,
        *,
        today: date,
        blogger_id: UUID | None = None,
        pr_id: UUID | None = None,
    ) -> int:
        """当天 status ∈ {created, sent} 计数（频控，BR-U07-41/42）。"""
        stmt = (
            select(func.count())
            .select_from(WecomMessage)
            .where(
                WecomMessage.status.in_(("created", "sent")),
                text(
                    "(wecom_message.created_at AT TIME ZONE 'Asia/Shanghai')::date "
                    "= :today"
                ),
            )
        )
        if blogger_id is not None:
            stmt = stmt.where(WecomMessage.blogger_id == blogger_id)
        if pr_id is not None:
            stmt = stmt.where(WecomMessage.pr_id == pr_id)
        return int((await self._s.execute(stmt, {"today": today})).scalar_one())

    async def list_recent(
        self, *, limit: int = 50, offset: int = 0
    ) -> Sequence[WecomMessage]:
        stmt = (
            select(WecomMessage)
            .order_by(WecomMessage.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._s.execute(stmt)).scalars().all()


class NotificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, notification: Notification) -> None:
        self._s.add(notification)

    async def get(self, notification_id: UUID) -> Notification | None:
        return await self._s.get(Notification, notification_id)

    async def list_for_user(
        self,
        *,
        user_id: UUID,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Notification]:
        stmt = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            stmt = stmt.where(Notification.is_read.is_(False))
        stmt = (
            stmt.order_by(Notification.created_at.desc()).limit(limit).offset(offset)
        )
        return (await self._s.execute(stmt)).scalars().all()

    async def unread_count(self, *, user_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.user_id == user_id, Notification.is_read.is_(False)
            )
        )
        return int((await self._s.execute(stmt)).scalar_one())


class WecomAlertConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self) -> WecomAlertConfig | None:
        return (
            await self._s.execute(select(WecomAlertConfig).limit(1))
        ).scalar_one_or_none()


class WecomAlertLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, row: WecomAlertLog) -> None:
        self._s.add(row)

    async def exists(
        self, *, alert_type: str, entity_ref: str, period_key: str
    ) -> bool:
        stmt = (
            select(func.count())
            .select_from(WecomAlertLog)
            .where(
                WecomAlertLog.alert_type == alert_type,
                WecomAlertLog.entity_ref == entity_ref,
                WecomAlertLog.period_key == period_key,
            )
        )
        return int((await self._s.execute(stmt)).scalar_one()) > 0


__all__ = [
    "MessageTemplateRepository",
    "NotificationRepository",
    "WecomAlertConfigRepository",
    "WecomAlertLogRepository",
    "WecomConfigRepository",
    "WecomContactRepository",
    "WecomMessageRepository",
]
