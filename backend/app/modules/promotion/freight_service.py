"""召回运费事务；独立于服务费SettlementRequested/SettlementPaid链。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import Attachment
from app.core.exceptions import FieldPermissionDenied, PermissionDeniedError
from app.core.security.field_permissions import can_write_field
from app.modules.auth.models import User
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.flow.matrix import FlowActor
from app.modules.promotion.exceptions import (
    PromotionCompletedReadonlyError,
    PromotionInactiveError,
    PromotionNotFoundError,
)
from app.modules.promotion.freight_models import ReturnFreightPayment
from app.modules.promotion.freight_repository import ReturnFreightRepository
from app.modules.promotion.freight_rules import (
    FreightNotFoundError,
    FreightStateConflictError,
    FreightValidationError,
    freight_transition,
)
from app.modules.promotion.freight_schemas import FreightResubmitRequest, FreightSubmitRequest
from app.modules.promotion.models import Promotion
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.schemas import PromotionUpdate
from app.modules.promotion.service_parts.base import PromotionServiceBase


@dataclass(frozen=True)
class FreightWriteContext:
    promotion: Promotion
    freight: ReturnFreightPayment | None


async def lock_freight_context(
    session: AsyncSession, promotion_id: UUID, user: User
) -> FreightWriteContext:
    """统一锁顺序，不能先锁付款单再锁promotion；调用方另外判写入资格。"""
    promotion = await PromotionRepository(session).get_for_write(
        promotion_id, tenant_id=user.tenant_id, include_inactive=True
    )
    if promotion is None:
        raise PromotionNotFoundError()
    freight = await ReturnFreightRepository(session).by_promotion(
        promotion_id, tenant_id=user.tenant_id, for_update=True
    )
    return FreightWriteContext(promotion, freight)


def ensure_scope(actor: FlowActor, resource: str, action: str) -> None:
    if not actor.perms.has(resource, action):
        raise PermissionDeniedError()


def ensure_freight_amount_write(actor: FlowActor) -> None:
    if not can_write_field("promotion", "cost_snapshot", actor.field_ctx):
        raise FieldPermissionDenied(field="cost_snapshot", entity="promotion")


def ensure_active(promotion: Promotion) -> None:
    if not promotion.is_active or promotion.publish_status == "已删除":
        raise PromotionInactiveError("推广已停用或删除，只读")


class ReturnFreightService(PromotionServiceBase):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        self.freights = ReturnFreightRepository(session)

    async def _locked(self, freight_id: UUID, user: User) -> FreightWriteContext:
        original = await self.freights.get(freight_id, tenant_id=user.tenant_id)
        if original is None:
            raise FreightNotFoundError()
        context = await lock_freight_context(self._session, original.promotion_id, user)
        if context.freight is None or context.freight.id != freight_id:
            raise FreightStateConflictError()
        return context

    async def _ready_images(self, promotion: Promotion) -> tuple[UUID, UUID]:
        ids = (promotion.payment_qr_attachment_id, promotion.recall_waybill_attachment_id)
        if None in ids:
            raise FreightValidationError("寄回运费需要收款码和寄回单号截图")
        rows = (
            await self._session.execute(
                select(Attachment).where(
                    Attachment.tenant_id == promotion.tenant_id, Attachment.id.in_(ids)
                )
            )
        ).scalars()
        attachments = {row.id: row for row in rows}
        for aid, purpose in zip(
            ids, ("promotion_payment_qr", "recall_waybill_screenshot"), strict=True
        ):
            assert aid is not None
            attachment = attachments.get(aid)
            if (
                attachment is None
                or attachment.status != "ready"
                or attachment.bucket != "private"
                or attachment.purpose != purpose
                or attachment.mime_type not in ("image/jpeg", "image/png", "image/webp")
            ):
                raise FreightValidationError("收款码或寄回截图无效，请重新上传")
        assert ids[0] is not None and ids[1] is not None
        return ids[0], ids[1]

    async def set_amount_locked(
        self, promotion: Promotion, amount: Decimal, user: User, *, payer: str = "博主垫付"
    ) -> None:
        before = self._amount_snapshot(promotion)
        promotion.return_shipping_fee = amount
        promotion.return_shipping_payer = payer
        await self._session.flush()
        self._log_amount_changes(
            promotion_id=promotion.id,
            tenant_id=promotion.tenant_id,
            user_id=user.id,
            before=before,
            after=self._amount_snapshot(promotion),
            payload=PromotionUpdate(return_shipping_fee=amount),
        )

    async def _event(
        self,
        freight: ReturnFreightPayment,
        promotion: Promotion,
        user: User,
        event_type: LineEventType,
        *,
        note: str | None = None,
        snapshots: bool = False,
        from_state: str | None = None,
        proof_id: UUID | None = None,
    ) -> None:
        refs: list[tuple[UUID | None, str | None]] = (
            [
                (freight.payment_qr_attachment_id, "payment_qr"),
                (freight.waybill_attachment_id, "waybill"),
            ]
            if snapshots
            else [(proof_id, "payment_proof" if proof_id else None)]
        )
        for attachment_id, role in refs:
            await LineEventRepository(self._session).append(
                tenant_id=promotion.tenant_id,
                line_id=promotion.line_id,
                promotion_id=promotion.id,
                actor_id=user.id,
                event_type=event_type,
                from_state=from_state,
                to_state=freight.status,
                note=note,
                attachment_id=attachment_id,
                payload={
                    "freight_payment_id": str(freight.id),
                    "freight_amount": f"{freight.amount:.2f}",
                    "snapshot_role": role,
                },
            )
        await self._audit.log(
            action=event_type.value,
            resource="return_freight_payment",
            resource_id=freight.id,
            user_id=user.id,
            after={"status": freight.status, "amount_changed": True},
        )

    async def submit_locked(
        self, promotion: Promotion, *, amount: Decimal | None, user: User, automatic: bool
    ) -> ReturnFreightPayment:
        actor = await self._flow_actor(user)
        ensure_scope(actor, "promotion", "write")
        ensure_freight_amount_write(actor)
        ensure_active(promotion)
        if promotion.recall_status != "召回成功":
            raise FreightValidationError("召回成功后才能提交寄回运费")
        if promotion.completed_at is not None and not automatic and not actor.is_star:
            raise PromotionCompletedReadonlyError("已完结且没有运费单，只能由管理员补提")
        amount = amount if amount is not None else promotion.return_shipping_fee
        if amount is None or amount <= 0:
            raise FreightValidationError("寄回运费金额必须大于0")
        qr, waybill = await self._ready_images(promotion)
        if (
            await self.freights.by_promotion(
                promotion.id, tenant_id=user.tenant_id, for_update=True
            )
            is not None
        ):
            raise FreightStateConflictError("此推广已有寄回运费单，请在原单处理")
        freight = await self.freights.insert_once(
            {
                "id": uuid4(),
                "tenant_id": user.tenant_id,
                "promotion_id": promotion.id,
                "amount": amount,
                "status": "待审核",
                "payment_qr_attachment_id": qr,
                "waybill_attachment_id": waybill,
                "submitted_by": user.id,
                "submitted_at": datetime.now(UTC),
            }
        )
        if freight is None:
            raise FreightStateConflictError("此推广已有寄回运费单")
        await self.set_amount_locked(promotion, amount, user)
        await self._event(freight, promotion, user, LineEventType.FREIGHT_SUBMITTED, snapshots=True)
        return freight

    async def submit(
        self, promotion_id: UUID, payload: FreightSubmitRequest, user: User
    ) -> ReturnFreightPayment:
        context = await lock_freight_context(self._session, promotion_id, user)
        if context.freight is not None:
            raise FreightStateConflictError("此推广已有寄回运费单")
        freight = await self.submit_locked(
            context.promotion, amount=payload.amount, user=user, automatic=False
        )
        await self._session.commit()
        return freight

    async def resubmit(
        self, freight_id: UUID, payload: FreightResubmitRequest, user: User
    ) -> ReturnFreightPayment:
        context = await self._locked(freight_id, user)
        promotion, freight = context.promotion, context.freight
        assert freight is not None
        actor = await self._flow_actor(user)
        ensure_scope(actor, "promotion", "write")
        ensure_freight_amount_write(actor)
        ensure_active(promotion)
        target = freight_transition(freight.status, "resubmit")
        # 完结专用例外仅此已驳回动作；绝不清除completed_at，也不放开通用PATCH。
        qr, waybill = await self._ready_images(promotion)
        amount = payload.amount if payload.amount is not None else freight.amount
        updated = await self.freights.transition(
            freight.id,
            tenant_id=user.tenant_id,
            from_status="已驳回",
            to_status=target,
            values={
                "amount": amount,
                "payment_qr_attachment_id": qr,
                "waybill_attachment_id": waybill,
                "submitted_by": user.id,
                "submitted_at": datetime.now(UTC),
                "reviewed_by": None,
                "reviewed_at": None,
                "remark": payload.note,
            },
        )
        if updated is None:
            raise FreightStateConflictError()
        await self.set_amount_locked(promotion, amount, user)
        await self._event(
            updated,
            promotion,
            user,
            LineEventType.FREIGHT_RESUBMITTED,
            note=payload.note,
            snapshots=True,
            from_state="已驳回",
        )
        await self._session.commit()
        return updated
