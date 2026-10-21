"""召回运费事务；独立于服务费SettlementRequested/SettlementPaid链。"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.attachment import Attachment, check_image_payload
from app.core.exceptions import FieldPermissionDenied, PermissionDeniedError
from app.core.security.field_permissions import can_read_field, can_write_field
from app.modules.auth.models import User
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.flow.matrix import FlowActor, require, ui_for
from app.modules.promotion.display_name import promotion_display_short_name
from app.modules.promotion.exceptions import (
    PromotionCompletedReadonlyError,
    PromotionInactiveError,
    PromotionNotFoundError,
)
from app.modules.promotion.freight_flow import freight_doc
from app.modules.promotion.freight_models import ReturnFreightPayment
from app.modules.promotion.freight_repository import FreightReadRecord, ReturnFreightRepository
from app.modules.promotion.freight_rules import (
    FreightNotFoundError,
    FreightStateConflictError,
    FreightValidationError,
    freight_transition,
)
from app.modules.promotion.freight_schemas import (
    FreightListFilters,
    FreightRejectRequest,
    FreightResubmitRequest,
    FreightReviewRequest,
    FreightStatus,
    FreightSubmitRequest,
    ReturnFreightPaymentPage,
    ReturnFreightPaymentResponse,
)
from app.modules.promotion.models import Promotion
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.schemas import PromotionUpdate
from app.modules.promotion.service_parts.base import PromotionServiceBase
from app.modules.promotion.urge_calculator import get_today


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

    async def review(
        self, freight_id: UUID, payload: FreightReviewRequest, user: User
    ) -> ReturnFreightPayment:
        actor = await self._flow_actor(user)
        ensure_scope(actor, "promotion_review", "approve")
        context = await self._locked(freight_id, user)
        freight, promotion = context.freight, context.promotion
        assert freight is not None
        target = freight_transition(freight.status, payload.action)
        negotiators = await self._repo.negotiator_ids([promotion.id])
        require(
            actor,
            freight_doc(freight.status, negotiators.get(promotion.id, promotion.pr_id)),
            "freight_review",
        )
        now = datetime.now(UTC)
        values = (
            {"reviewed_by": user.id, "reviewed_at": now}
            if payload.action == "approve"
            else {"reject_reason": payload.reason, "rejected_by": user.id, "rejected_at": now}
        )
        updated = await self.freights.transition(
            freight.id,
            tenant_id=user.tenant_id,
            from_status="待审核",
            to_status=target,
            values=values,
        )
        if updated is None:
            raise FreightStateConflictError()
        await self._event(
            updated,
            promotion,
            user,
            LineEventType.FREIGHT_APPROVED
            if payload.action == "approve"
            else LineEventType.FREIGHT_REJECTED,
            note=payload.reason,
            from_state="待审核",
        )
        await self._session.commit()
        return updated

    async def reject_payment(
        self, freight_id: UUID, payload: FreightRejectRequest, user: User
    ) -> ReturnFreightPayment:
        ensure_scope(await self._flow_actor(user), "finance.settlement", "pay")
        context = await self._locked(freight_id, user)
        freight = context.freight
        assert freight is not None
        target = freight_transition(freight.status, "reject_payment")
        updated = await self.freights.transition(
            freight.id,
            tenant_id=user.tenant_id,
            from_status="待付款",
            to_status=target,
            values={
                "reject_reason": payload.reason,
                "rejected_by": user.id,
                "rejected_at": datetime.now(UTC),
            },
        )
        if updated is None:
            raise FreightStateConflictError()
        await self._event(
            updated,
            context.promotion,
            user,
            LineEventType.FREIGHT_REJECTED,
            note=payload.reason,
            from_state="待付款",
        )
        await self._session.commit()
        return updated

    async def pay(
        self,
        freight_id: UUID,
        *,
        filename: str | None,
        mime_type: str | None,
        data: bytes,
        user: User,
    ) -> ReturnFreightPayment:
        from fastapi.concurrency import run_in_threadpool

        from app.modules.promotion.service_parts.base import log

        ensure_scope(await self._flow_actor(user), "finance.settlement", "pay")
        context = await self._locked(freight_id, user)
        freight = context.freight
        assert freight is not None
        target = freight_transition(freight.status, "pay")
        problem = check_image_payload(
            data=data,
            mime_type=mime_type,
            filename=filename,
            max_bytes=10 * 1024 * 1024,
            label="付款凭证",
        )
        if problem:
            raise FreightValidationError(problem)
        key: str | None = None
        try:
            attachment, _ = await self._attachment_service.create_upload_record(
                session=self._session,
                tenant_id=user.tenant_id,
                created_by=user.id,
                bucket="private",
                purpose="settlement_proof",
                filename=filename,
                mime_type=str(mime_type),
                size_bytes=len(data),
            )
            key = attachment.r2_key
            await run_in_threadpool(
                self._attachment_service.upload_bytes,
                data,
                bucket="private",
                key=key,
                content_type=str(mime_type),
            )
            await self._attachment_service.mark_uploaded(
                session=self._session, attachment_id=attachment.id, tenant_id=user.tenant_id
            )
            updated = await self.freights.transition(
                freight.id,
                tenant_id=user.tenant_id,
                from_status="待付款",
                to_status=target,
                values={
                    "payment_date": get_today(),
                    "payment_proof_attachment_id": attachment.id,
                    "paid_by": user.id,
                    "paid_at": datetime.now(UTC),
                },
            )
            if updated is None:
                raise FreightStateConflictError()
            # R33只写自己主线事件，绝不发送SettlementPaid或修改服务费结款链。
            await self._event(
                updated,
                context.promotion,
                user,
                LineEventType.FREIGHT_PAID,
                from_state="待付款",
                proof_id=attachment.id,
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            if key is not None:
                try:
                    await run_in_threadpool(self._attachment_service.delete, "private", key)
                except Exception:
                    log.warning("freight_proof_compensation_delete_failed")
            raise
        return updated

    async def responses(
        self, rows: list[FreightReadRecord], user: User
    ) -> list[ReturnFreightPaymentResponse]:
        actor = await self._flow_actor(user)
        can_amount = can_read_field("promotion", "cost_snapshot", actor.field_ctx)
        can_images = can_read_field("promotion", "payment_qr", actor.field_ctx)
        people = {
            uid
            for row in rows
            for uid in (
                row.freight.submitted_by,
                row.freight.reviewed_by,
                row.freight.rejected_by,
                row.freight.paid_by,
            )
            if uid is not None
        }
        names = await self._repo.user_names(list(people)) if people else {}
        negotiators = (
            await self._repo.negotiator_ids([row.promotion.id for row in rows]) if rows else {}
        )
        urls: dict[UUID, str | None] = {}
        if can_images:
            ids = {
                aid
                for row in rows
                for aid in (
                    row.freight.payment_qr_attachment_id,
                    row.freight.waybill_attachment_id,
                    row.freight.payment_proof_attachment_id,
                )
                if aid is not None
            }
            if ids:
                attachments = (
                    await self._session.execute(
                        select(Attachment).where(
                            Attachment.id.in_(ids),
                            Attachment.tenant_id == user.tenant_id,
                            Attachment.bucket == "private",
                            Attachment.status == "ready",
                        )
                    )
                ).scalars()
                for attachment in attachments:
                    try:
                        urls[attachment.id] = self._attachment_service.get_signed_url(
                            "private", attachment.r2_key, expires_in=900
                        )
                    except Exception:
                        urls[attachment.id] = None
        result = []
        for row in rows:
            f, p = row.freight, row.promotion
            ui = ui_for(actor, freight_doc(f.status, negotiators.get(p.id, p.pr_id))).to_dict(
                view="detail"
            )
            result.append(
                ReturnFreightPaymentResponse(
                    id=f.id,
                    promotion_id=p.id,
                    line_id=p.line_id,
                    promotion_internal_code=p.internal_code,
                    blogger_nickname=row.blogger_nickname,
                    display_short_name=promotion_display_short_name(
                        goods_short_name=row.goods_short_name,
                        style_short_name_snapshot=p.style_short_name_snapshot,
                    ),
                    amount=f.amount if can_amount else None,
                    status=cast(FreightStatus, f.status),
                    payment_qr_attachment_id=f.payment_qr_attachment_id if can_images else None,
                    waybill_attachment_id=f.waybill_attachment_id if can_images else None,
                    payment_proof_attachment_id=f.payment_proof_attachment_id
                    if can_images
                    else None,
                    payment_qr_signed_url=urls.get(f.payment_qr_attachment_id),
                    waybill_signed_url=urls.get(f.waybill_attachment_id),
                    payment_proof_signed_url=urls.get(f.payment_proof_attachment_id)
                    if f.payment_proof_attachment_id
                    else None,
                    submitted_by=f.submitted_by,
                    submitted_by_name=names.get(f.submitted_by) if f.submitted_by else None,
                    submitted_at=f.submitted_at,
                    reviewed_by=f.reviewed_by,
                    reviewed_by_name=names.get(f.reviewed_by) if f.reviewed_by else None,
                    reviewed_at=f.reviewed_at,
                    rejected_by=f.rejected_by,
                    rejected_by_name=names.get(f.rejected_by) if f.rejected_by else None,
                    rejected_at=f.rejected_at,
                    reject_reason=f.reject_reason,
                    paid_by=f.paid_by,
                    paid_by_name=names.get(f.paid_by) if f.paid_by else None,
                    paid_at=f.paid_at,
                    payment_date=f.payment_date,
                    remark=f.remark,
                    ui=ui,
                )
            )
        return result

    async def response(
        self, freight: ReturnFreightPayment, user: User
    ) -> ReturnFreightPaymentResponse:
        rows, _ = await self.freights.read_records(
            tenant_id=user.tenant_id, filters=FreightListFilters(), ids=[freight.id]
        )
        if not rows:
            raise FreightNotFoundError()
        return (await self.responses(rows, user))[0]

    async def get(self, freight_id: UUID, user: User) -> ReturnFreightPaymentResponse:
        actor = await self._flow_actor(user)
        if not (
            actor.perms.has("finance.settlement", "read") or actor.perms.has("promotion", "read")
        ):
            raise FreightNotFoundError()
        freight = await self.freights.get(freight_id, tenant_id=user.tenant_id)
        if freight is None:
            raise FreightNotFoundError()
        return await self.response(freight, user)

    async def page(
        self, filters: FreightListFilters, *, page: int, page_size: int, user: User
    ) -> ReturnFreightPaymentPage:
        ensure_scope(await self._flow_actor(user), "finance.settlement", "read")
        rows, total = await self.freights.read_records(
            tenant_id=user.tenant_id, filters=filters, page=page, page_size=page_size
        )
        return ReturnFreightPaymentPage(
            items=await self.responses(rows, user), total=total, page=page, page_size=page_size
        )
