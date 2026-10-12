"""时间线读接口的只读查询（流程线设计 6.5「性能」）。

事件本身走 ``LineEventRepository.list_by_line``（只追加仓储只有两个公开方法）；这里放事件之外的几条：
- ``header``：线上的推广单 + 谈款 + 有没有结款单，一条 SQL（一条线最多一张推广单、一张谈款：``uq_*_line``）
- ``attachments``：过了第 2、3 层的附件一次批量取
- ``actor_names``：事件动作人的显示名一次批量取
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, exists, literal, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import Attachment
from app.modules.auth.models import User
from app.modules.finance.models import Settlement
from app.modules.negotiation.models import Negotiation
from app.modules.promotion.models import Promotion


@dataclass(frozen=True)
class LineHeader:
    promotion: Promotion | None
    negotiation: Negotiation | None
    has_settlement: bool


@dataclass(frozen=True)
class AttachmentRef:
    id: UUID
    r2_key: str
    mime_type: str


class LineReadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def header(self, *, tenant_id: UUID, line_id: UUID) -> LineHeader:
        """以 line_id 为锚，左连推广单与谈款（两边都可能没有）；结款单只问有没有。"""
        anchor = select(literal(line_id, PGUUID(as_uuid=True)).label("line_id")).subquery()
        has_settlement = (
            exists()
            .where(Settlement.tenant_id == tenant_id, Settlement.promotion_id == Promotion.id)
            .correlate(Promotion)
        )
        stmt = (
            select(Promotion, Negotiation, has_settlement.label("has_settlement"))
            .select_from(anchor)
            .outerjoin(
                Promotion,
                and_(Promotion.tenant_id == tenant_id, Promotion.line_id == anchor.c.line_id),
            )
            .outerjoin(
                Negotiation,
                and_(Negotiation.tenant_id == tenant_id, Negotiation.line_id == anchor.c.line_id),
            )
        )
        row = (await self._session.execute(stmt)).one()
        return LineHeader(
            promotion=row[0], negotiation=row[1], has_settlement=bool(row.has_settlement)
        )

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


__all__ = ["AttachmentRef", "LineHeader", "LineReadRepository"]
