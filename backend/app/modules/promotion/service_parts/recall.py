"""PromotionService：召回：发起 / 成功 / 失败（从 service.py 搬出）。"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.attachment import Attachment, check_image_payload
from app.core.exceptions import FieldPermissionDenied
from app.core.metrics import (
    promotion_state_transitions_total,
)
from app.core.security.field_permissions import can_write_field
from app.modules.auth.models import User
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.promotion.enums import (
    PublishStatus,
    RecallStatus,
)
from app.modules.promotion.exceptions import (
    PromotionCompletedReadonlyError,
    StateTransitionConflictError,
)
from app.modules.promotion.freight_rules import (
    FreightStateConflictError,
    FreightValidationError,
    continuing_published_recall,
)
from app.modules.promotion.freight_service import (
    FreightWriteContext,
    ReturnFreightService,
    ensure_active,
    ensure_freight_amount_write,
    ensure_scope,
    lock_freight_context,
)
from app.modules.promotion.schemas import (
    PromotionRecallStartRequest,
    PromotionRecallSuccessRequest,
    PromotionResponse,
)
from app.modules.promotion.service_parts.base import (
    PromotionServiceBase,
)
from app.modules.promotion.state_machines import (
    RecallStatusMachine,
)


class PromotionRecallMixin(PromotionServiceBase):
    """召回：发起 / 成功 / 失败。"""

    async def _recall_context(
        self, promotion_id: UUID, user: User, *, waybill: bool = False
    ) -> FreightWriteContext:
        context = await lock_freight_context(self._session, promotion_id, user)
        promotion, freight = context.promotion, context.freight
        actor = await self._flow_actor(user)
        ensure_scope(actor, "promotion", "write")
        ensure_active(promotion)
        status = freight.status if freight else None
        if waybill and status in ("待审核", "待付款", "已付款"):
            raise FreightStateConflictError("寄回截图已快照到付款单，驳回后才可更新")
        if promotion.completed_at is not None and not actor.is_star:
            continuing = continuing_published_recall(
                publish_status=promotion.publish_status,
                recall_status=promotion.recall_status,
                is_active=promotion.is_active,
            )
            if not continuing and not (waybill and status == "已驳回"):
                raise PromotionCompletedReadonlyError("已完结，只读")
        if waybill:
            if promotion.recall_status not in ("召回中", "召回成功"):
                raise StateTransitionConflictError("召回中或召回成功后才能传寄回截图")
            if not can_write_field("promotion", "payment_qr", actor.field_ctx):
                raise FieldPermissionDenied(field="payment_qr", entity="promotion")
        elif promotion.recall_status != "召回中":
            raise StateTransitionConflictError("只有召回中才能登记召回结果")
        return context

    async def upload_recall_waybill(
        self,
        promotion_id: UUID,
        *,
        filename: str | None,
        mime_type: str | None,
        data: bytes,
        user: User,
    ) -> PromotionResponse:
        from fastapi.concurrency import run_in_threadpool

        from app.modules.promotion.freight_rules import FreightValidationError
        from app.modules.promotion.service_parts.base import log

        context = await self._recall_context(promotion_id, user, waybill=True)
        promotion = context.promotion
        problem = check_image_payload(
            data=data,
            mime_type=mime_type,
            filename=filename,
            max_bytes=10 * 1024 * 1024,
            label="寄回单号截图",
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
                purpose="recall_waybill_screenshot",
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
            promotion.recall_waybill_attachment_id = attachment.id
            await LineEventRepository(self._session).append(
                tenant_id=promotion.tenant_id,
                line_id=promotion.line_id,
                event_type=LineEventType.RECALL_WAYBILL_UPLOADED,
                actor_id=user.id,
                promotion_id=promotion.id,
                attachment_id=attachment.id,
            )
            await self._audit.log(
                action="recall.waybill_uploaded",
                resource="promotion",
                resource_id=promotion.id,
                user_id=user.id,
                after={"attachment_changed": True},
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            if key is not None:
                try:
                    await run_in_threadpool(self._attachment_service.delete, "private", key)
                except Exception:
                    log.warning("recall_waybill_compensation_delete_failed")
            raise
        return await self._to_response(promotion, user)

    async def start_recall(
        self,
        promotion_id: UUID,
        payload: PromotionRecallStartRequest,
        user: User,
    ) -> PromotionResponse:
        """C1/C2：行锁内取消/关催发/发起召回，一次提交。"""
        promotion = await self._writable_promotion(promotion_id, user)
        ensure_scope(await self._flow_actor(user), "promotion", "write")
        ensure_active(promotion)

        # BR-U04-24 跨状态机校验
        if promotion.publish_status not in (
            PublishStatus.PUBLISHED.value,
            PublishStatus.CANCELLED.value,
            PublishStatus.UNPUBLISHED.value,
        ):
            raise StateTransitionConflictError(
                "当前发布状态不能发起召回",
                details={
                    "publish_status": promotion.publish_status,
                    "required": ["已发布", "已取消"],
                },
            )

        from_value = promotion.recall_status
        # 状态机校验：未召回 → 召回中  OR  召回失败 → 召回中
        if from_value not in (
            RecallStatus.NOT_RECALLED.value,
            RecallStatus.RECALLED_FAILURE.value,
        ):
            raise StateTransitionConflictError(
                f"recall_status={from_value} 不允许启动召回",
                details={"recall_status": from_value},
            )

        RecallStatusMachine.assert_can_transition(
            from_state=from_value,
            to_state=RecallStatus.RECALLING.value,
            action="start_recall",
        )

        events = LineEventRepository(self._session)
        if promotion.publish_status == PublishStatus.UNPUBLISHED.value:
            if promotion.ship_status not in ("已发货", None):
                raise FreightValidationError("尚未发货，直接取消即可")
            # 不调用会自己commit的cancel，也不复用吞催发异常的辅助方法。
            promotion.publish_status = PublishStatus.CANCELLED.value
            promotion.cancel_reason = payload.recall_reason
            await self._session.flush()
            await events.append(
                tenant_id=promotion.tenant_id,
                line_id=promotion.line_id,
                event_type=LineEventType.PROMOTION_CANCELLED,
                actor_id=user.id,
                promotion_id=promotion.id,
                from_state="未发布",
                to_state="已取消",
                note=payload.recall_reason,
                payload={"by_cancel": True},
            )
            from app.modules.urge.enums import UrgeCloseReason
            from app.modules.urge.service import UrgeService

            task_id = await UrgeService(self._session).close_for_promotion(
                promotion_id=promotion.id,
                tenant_id=user.tenant_id,
                reason=UrgeCloseReason.CANCELLED,
            )
            if task_id is not None:
                await events.append(
                    tenant_id=promotion.tenant_id,
                    line_id=promotion.line_id,
                    event_type=LineEventType.URGE_CLOSED,
                    actor_id=user.id,
                    promotion_id=promotion.id,
                    payload={"urge_task_id": str(task_id), "close_reason": "已取消"},
                )

        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="recall_status",
            from_state_value=from_value,
            to_state_value=RecallStatus.RECALLING.value,
            extra_fields=(
                {"recall_reason": payload.recall_reason} if payload.recall_reason else None
            ),
        )
        if updated is None:
            raise StateTransitionConflictError(
                "推广状态已变更，请刷新后重试",
                details={"promotion_id": str(promotion_id)},
            )

        promotion_state_transitions_total.labels(
            from_state=from_value,
            to_state=RecallStatus.RECALLING.value,
            status_field="recall",
        ).inc()

        # 取消与开始事件处在相同事务，任一步失败均由请求事务回滚。
        await LineEventRepository(self._session).append(
            tenant_id=promotion.tenant_id,
            line_id=promotion.line_id,
            event_type=LineEventType.RECALL_STARTED,
            actor_id=user.id,
            promotion_id=promotion_id,
            from_state=from_value,
            to_state=RecallStatus.RECALLING.value,
            note=payload.recall_reason or None,
        )

        await self._audit.log(
            action="promotion.start_recall",
            resource="promotion",
            resource_id=promotion_id,
            after={"recall_status": RecallStatus.RECALLING.value},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user)

    async def recall_success(
        self, promotion_id: UUID, payload: PromotionRecallSuccessRequest, user: User
    ) -> PromotionResponse:
        """EP05-S09: 召回成功（终态）."""
        return await self._recall_finish(
            promotion_id=promotion_id,
            user=user,
            to_state=RecallStatus.RECALLED_SUCCESS.value,
            action_log="promotion.recall_success",
            success=payload,
            remark=payload.remark,
        )

    async def recall_failure(
        self, promotion_id: UUID, user: User, *, remark: str | None = None
    ) -> PromotionResponse:
        """EP05-S09: 召回失败（可重试）."""
        return await self._recall_finish(
            promotion_id=promotion_id,
            user=user,
            to_state=RecallStatus.RECALLED_FAILURE.value,
            action_log="promotion.recall_failure",
            remark=remark,
        )

    async def _recall_finish(
        self,
        *,
        promotion_id: UUID,
        user: User,
        to_state: str,
        action_log: str,
        success: PromotionRecallSuccessRequest | None = None,
        remark: str | None = None,
    ) -> PromotionResponse:
        context = await self._recall_context(promotion_id, user)
        promotion = context.promotion
        freight_service = ReturnFreightService(self._session)
        if success is not None:
            ensure_freight_amount_write(await self._flow_actor(user))
            if context.freight is not None:
                raise FreightStateConflictError("此推广已存在寄回运费单")
            # 0元和到付也要真实有效的寄回图；正金额垫付额外检查收款码。
            waybill = await self._session.scalar(
                select(Attachment).where(
                    Attachment.id == promotion.recall_waybill_attachment_id,
                    Attachment.tenant_id == user.tenant_id,
                    Attachment.status == "ready",
                    Attachment.bucket == "private",
                    Attachment.purpose == "recall_waybill_screenshot",
                    Attachment.mime_type.in_(("image/jpeg", "image/png", "image/webp")),
                )
            )
            if waybill is None:
                raise FreightValidationError("请先上传寄回单号截图")
            if success.return_shipping_fee > 0 and success.return_shipping_payer == "博主垫付":
                await freight_service._ready_images(promotion)

        action_name = (
            "recall_success"
            if to_state == RecallStatus.RECALLED_SUCCESS.value
            else "recall_failure"
        )
        RecallStatusMachine.assert_can_transition(
            from_state=promotion.recall_status,
            to_state=to_state,
            action=action_name,
        )

        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="recall_status",
            from_state_value=RecallStatus.RECALLING.value,
            to_state_value=to_state,
        )
        if updated is None:
            raise StateTransitionConflictError(
                "召回状态已变更，请刷新后重试",
                details={"promotion_id": str(promotion_id)},
            )

        if success is not None:
            await freight_service.set_amount_locked(
                updated, success.return_shipping_fee, user, payer=success.return_shipping_payer
            )

        promotion_state_transitions_total.labels(
            from_state=RecallStatus.RECALLING.value,
            to_state=to_state,
            status_field="recall",
        ).inc()

        # 成功与失败备注都留痕，金额按字段规则裁剪。
        await LineEventRepository(self._session).append(
            tenant_id=promotion.tenant_id,
            line_id=promotion.line_id,
            event_type=(
                LineEventType.RECALL_SUCCEEDED
                if to_state == RecallStatus.RECALLED_SUCCESS.value
                else LineEventType.RECALL_FAILED
            ),
            actor_id=user.id,
            promotion_id=promotion_id,
            from_state=RecallStatus.RECALLING.value,
            to_state=to_state,
            note=remark,
            attachment_id=promotion.recall_waybill_attachment_id if success is not None else None,
            payload=(
                {
                    "return_shipping_fee": f"{success.return_shipping_fee:.2f}",
                    "return_shipping_payer": success.return_shipping_payer,
                }
                if success is not None
                else None
            ),
        )

        await self._audit.log(
            action=action_log,
            resource="promotion",
            resource_id=promotion_id,
            after={"recall_status": to_state},
            user_id=user.id,
        )
        if to_state == RecallStatus.RECALLED_SUCCESS.value:
            assert success is not None
            if success.return_shipping_payer == "博主垫付" and success.return_shipping_fee > 0:
                # 已发布召回可能之前已正常完结：自动建单与maybe_complete返回值无关。
                await freight_service.submit_locked(
                    updated, amount=success.return_shipping_fee, user=user, automatic=True
                )
            await self.maybe_complete(promotion_id, user)
        await self._session.commit()
        return await self._to_response(updated, user)
