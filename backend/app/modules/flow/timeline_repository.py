"""时间线读接口的只读查询（流程线设计 6.5「性能」）。

事件本身走 ``LineEventRepository.list_by_line``（只追加仓储只有两个公开方法）；这里放事件之外的几条：
- ``header``：线上的推广单 + 谈款 + 结款单（有没有、付款日期），一条 SQL（一条线最多一张推广单、一张谈款：``uq_*_line``；
  一张推广单最多一张结款单：``uq_settlement_promotion``）
- ``first_event_at``：这条线第一条事件的时间（``legacy`` 的截止点；只在事件被 500 条上限截断时才查）
- ``attachments``：过了第 2、3 层的附件一次批量取
- ``actor_names``：事件动作人的显示名一次批量取
- ``ship_update_flags``：仓库页「地址已更新 / 明细已更新」，一页一查（7.4 末尾）
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, literal, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import Attachment
from app.modules.auth.models import User
from app.modules.finance.models import Settlement
from app.modules.flow.line_events import LineEventType
from app.modules.flow.models import LineEvent
from app.modules.negotiation.models import Negotiation
from app.modules.promotion.models import Promotion


@dataclass(frozen=True)
class LineHeader:
    promotion: Promotion | None
    negotiation: Negotiation | None
    has_settlement: bool
    settlement_payment_date: date | None


@dataclass(frozen=True)
class AttachmentRef:
    id: UUID
    r2_key: str
    mime_type: str


@dataclass(frozen=True)
class ShipUpdateFlags:
    receiver: bool
    items: bool


class LineReadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def header(self, *, tenant_id: UUID, line_id: UUID) -> LineHeader:
        """以 line_id 为锚，左连推广单与谈款（两边都可能没有），再左连推广单的结款单。"""
        anchor = select(literal(line_id, PGUUID(as_uuid=True)).label("line_id")).subquery()
        stmt = (
            select(
                Promotion,
                Negotiation,
                Settlement.id,
                Settlement.payment_date,
            )
            .select_from(anchor)
            .outerjoin(
                Promotion,
                and_(Promotion.tenant_id == tenant_id, Promotion.line_id == anchor.c.line_id),
            )
            .outerjoin(
                Negotiation,
                and_(Negotiation.tenant_id == tenant_id, Negotiation.line_id == anchor.c.line_id),
            )
            .outerjoin(
                Settlement,
                and_(Settlement.tenant_id == tenant_id, Settlement.promotion_id == Promotion.id),
            )
        )
        row = (await self._session.execute(stmt)).one()
        return LineHeader(
            promotion=row[0],
            negotiation=row[1],
            has_settlement=row[2] is not None,
            settlement_payment_date=row[3],
        )

    async def first_event_at(self, *, tenant_id: UUID, line_id: UUID) -> datetime | None:
        """按 ``seq`` 取第一条（走 ``idx_line_event_line``）；同一事务的几条 ``created_at`` 相同。"""
        stmt = (
            select(LineEvent.created_at)
            .where(LineEvent.tenant_id == tenant_id, LineEvent.line_id == line_id)
            .order_by(LineEvent.seq)
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def attachments(self, ids: Collection[UUID]) -> dict[UUID, AttachmentRef]:
        if not ids:
            return {}
        stmt = select(Attachment.id, Attachment.r2_key, Attachment.mime_type).where(
            Attachment.id.in_(list(ids))
        )
        return {
            r.id: AttachmentRef(id=r.id, r2_key=r.r2_key, mime_type=r.mime_type)
            for r in (await self._session.execute(stmt)).all()
        }

    async def actor_names(self, ids: Collection[UUID]) -> dict[UUID, str]:
        """显示名，没填回落用户名（已停用 / 软删的账号照样显示：时间线是历史记录）。"""
        if not ids:
            return {}
        stmt = select(User.id, User.display_name, User.username).where(User.id.in_(list(ids)))
        return {
            r.id: r.display_name or r.username for r in (await self._session.execute(stmt)).all()
        }

    async def ship_update_flags(
        self, *, tenant_id: UUID, line_ids: Collection[UUID]
    ) -> dict[UUID, ShipUpdateFlags]:
        """每条线最后一次 ``ship.receiver_changed`` / ``ship.items_changed`` 的 ``seq`` 是否大于最后一次
        ``ship.pushed``（没有推送事件按 0：PR-4 之前推送的单没有 pushed 事件）。

        「还在待打单」由调用方判（只传待打单的线）；没有这三类事件的线不在返回里（= 都是 false）。
        """
        if not line_ids:
            return {}

        def last(event_type: LineEventType) -> Any:
            return func.max(LineEvent.seq).filter(LineEvent.event_type == event_type.value)

        stmt = (
            select(
                LineEvent.line_id,
                last(LineEventType.SHIP_PUSHED),
                last(LineEventType.SHIP_RECEIVER_CHANGED),
                last(LineEventType.SHIP_ITEMS_CHANGED),
            )
            .where(
                LineEvent.tenant_id == tenant_id,
                LineEvent.line_id.in_(list(line_ids)),
                LineEvent.event_type.in_(
                    [
                        LineEventType.SHIP_PUSHED.value,
                        LineEventType.SHIP_RECEIVER_CHANGED.value,
                        LineEventType.SHIP_ITEMS_CHANGED.value,
                    ]
                ),
            )
            .group_by(LineEvent.line_id)
        )
        out: dict[UUID, ShipUpdateFlags] = {}
        for line_id, pushed, receiver, items in (await self._session.execute(stmt)).all():
            base = pushed or 0
            out[line_id] = ShipUpdateFlags(
                receiver=(receiver or 0) > base, items=(items or 0) > base
            )
        return out


__all__ = ["AttachmentRef", "LineHeader", "LineReadRepository", "ShipUpdateFlags"]
