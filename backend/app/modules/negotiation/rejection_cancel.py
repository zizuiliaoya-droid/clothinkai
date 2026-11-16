"""到期自动取消与事件同事务；条件 UPDATE 与原单重提互斥。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.negotiation.enums import NegotiationStatus
from app.modules.negotiation.models import Negotiation
from app.modules.negotiation.rejection_deadline import REJECTION_RETRY_WINDOW


async def cancel_expired_rejections(session: AsyncSession, tenant_id: UUID, now: datetime) -> int:
    """由调用方提交；重复执行不会重复取消或写事件，已重提单不会命中。"""
    rows = (
        await session.execute(
            update(Negotiation)
            .where(
                Negotiation.tenant_id == tenant_id,
                Negotiation.status == NegotiationStatus.REJECTED.value,
                func.coalesce(Negotiation.reviewed_at, Negotiation.updated_at)
                <= now - REJECTION_RETRY_WINDOW,
            )
            .values(
                status=NegotiationStatus.CANCELLED.value,
                reviewed_at=func.coalesce(Negotiation.reviewed_at, Negotiation.updated_at),
                updated_at=now,
            )
            .returning(Negotiation.id, Negotiation.line_id)
            .execution_options(synchronize_session=False)
        )
    ).all()
    events, audit = LineEventRepository(session), AuditService(session)
    for negotiation_id, line_id in rows:
        await events.append(
            tenant_id=tenant_id,
            line_id=line_id,
            negotiation_id=negotiation_id,
            event_type=LineEventType.NEGOTIATION_CANCELLED,
            actor_id=None,
            from_state=NegotiationStatus.REJECTED.value,
            to_state=NegotiationStatus.CANCELLED.value,
            note="主管驳回满 7 天未重新提交，系统自动取消",
        )
        await audit.log(
            "negotiation.cancel.expired",
            resource="negotiation",
            resource_id=negotiation_id,
            actor_type="system",
            tenant_id=tenant_id,
            before={"status": NegotiationStatus.REJECTED.value},
            after={"status": NegotiationStatus.CANCELLED.value},
        )
    return len(rows)
