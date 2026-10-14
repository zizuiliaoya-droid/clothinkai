"""PromotionService：结款链上 PR 的动作（流程线 3.4 R7 结款重提、R9 结款聊天截图）。"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.core import events as event_bus
from app.core.attachment import check_image_payload
from app.core.metrics import (
    promotion_state_transitions_total,
)
from app.modules.auth.models import User
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.flow.matrix import require
from app.modules.promotion.enums import SettlementStatus
from app.modules.promotion.events import SettlementResubmitted
from app.modules.promotion.exceptions import (
    PromotionNotFoundError,
    SettlementChatScreenshotRequiredError,
    StateTransitionConflictError,
)
from app.modules.promotion.schemas import (
    PromotionResponse,
    PromotionSettlementResubmitRequest,
)
from app.modules.promotion.service_parts.base import (
    PromotionServiceBase,
    _utcnow,
    log,
)
from app.modules.promotion.state_machines import (
    SettlementStatusMachine,
)

# 结款单（finance）的状态：F6 已驳回 → 待付款（推广模块不 import finance，同 review.py）
_SETTLEMENT_REJECTED = "已驳回"
_SETTLEMENT_PENDING_PAYMENT = "待付款"


class PromotionSettlementLinkMixin(PromotionServiceBase):
    """结款重提（R7）、上传结款聊天截图（R9）。"""

    async def resubmit_settlement(
        self,
        promotion_id: UUID,
        payload: PromotionSettlementResubmitRequest,
        user: User,
    ) -> PromotionResponse:
        """R7 结款重提：推广单 结款驳回 → 待付款，结款单 已驳回 → 待付款（F6），同一事务。

        校验顺序：矩阵 require（状态机只在结款驳回 → 权限 → ★ 收款码，缺 → 422 ``FLOW_GATE_MISSING``）
        → 条件 UPDATE 推广单 ``WHERE settlement_status = '结款驳回'``（0 行 → 409）→ 同 session 发
        ``SettlementResubmitted``（finance 监听 ``autocommit=False``，结款单不在已驳回 → 409，异常冒泡、整笔回滚）
        → 写 ``settlement.resubmitted``（说明进 note）→ 统一 commit。
        """
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        actor = await self._flow_actor(user)
        require(actor, await self._promotion_doc(promotion), "settlement_resubmit")
        SettlementStatusMachine.assert_can_transition(
            from_state=promotion.settlement_status,
            to_state=SettlementStatus.PENDING_PAYMENT.value,
            action="settlement_resubmit",
        )

        settlement = await self._repo.settlement_brief(
            tenant_id=promotion.tenant_id, promotion_id=promotion_id
        )
        if settlement is None:
            raise StateTransitionConflictError(
                "这张推广单没有结款单，不能重提结款",
                details={"promotion_id": str(promotion_id)},
            )

        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="settlement_status",
            from_state_value=SettlementStatus.SETTLEMENT_REJECTED.value,
            to_state_value=SettlementStatus.PENDING_PAYMENT.value,
        )
        if updated is None:
            raise StateTransitionConflictError(
                "结款状态已变更，请刷新后重试",
                details={"promotion_id": str(promotion_id)},
            )

        # F6：finance 监听在同一 session 里改结款单；不 catch，0 行的 409 / 缺监听都整笔回滚
        await event_bus.dispatch(
            SettlementResubmitted(
                event_id=uuid4(),
                timestamp=_utcnow(),
                tenant_id=promotion.tenant_id,
                promotion_id=promotion_id,
                settlement_id=settlement.settlement_id,
                note=payload.note,
                actor_id=user.id,
            ),
            session=self._session,
        )

        promotion_state_transitions_total.labels(
            from_state=SettlementStatus.SETTLEMENT_REJECTED.value,
            to_state=SettlementStatus.PENDING_PAYMENT.value,
            status_field="settlement",
        ).inc()

        # 时间线（6.3）：from / to 记结款单状态（同 settlement.rejected），说明进 note
        await LineEventRepository(self._session).append(
            tenant_id=promotion.tenant_id,
            line_id=promotion.line_id,
            event_type=LineEventType.SETTLEMENT_RESUBMITTED,
            actor_id=user.id,
            promotion_id=promotion_id,
            from_state=_SETTLEMENT_REJECTED,
            to_state=_SETTLEMENT_PENDING_PAYMENT,
            note=payload.note,
            payload={"settlement_id": str(settlement.settlement_id)},
        )

        await self._audit.log(
            action="promotion.settlement.resubmit",
            resource="promotion",
            resource_id=promotion_id,
            before={"settlement_status": SettlementStatus.SETTLEMENT_REJECTED.value},
            after={
                "settlement_status": SettlementStatus.PENDING_PAYMENT.value,
                "settlement_id": str(settlement.settlement_id),
                "has_note": True,
            },
            user_id=user.id,
        )

        await self._session.commit()
        return await self._to_response(updated, user)

    async def upload_settlement_chat(
        self,
        promotion_id: UUID,
        *,
        filename: str | None,
        mime_type: str | None,
        data: bytes,
        user: User,
    ) -> PromotionResponse:
        """R9 上传结款聊天截图：推广单 已付款 → 已结款，写截图与 ``settled_at``。

        校验顺序：矩阵 require（状态机只在已付款 → 422 ``ILLEGAL_STATE_TRANSITION``；权限）→ 截图必传
        （不合格 → 422 ``SETTLEMENT_CHAT_SCREENSHOT_REQUIRED``）→ 后端代传 → 条件 UPDATE
        ``WHERE settlement_status = '已付款'``（0 行 → 409）→ 写 ``promotion.settled``（附件 = 截图）→ commit。
        任何一步失败都回滚并删掉已传的对象（骨架同 ``upload_brand_comment``）。完结检查随 PR-7。
        """
        from fastapi.concurrency import run_in_threadpool

        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        actor = await self._flow_actor(user)
        require(actor, await self._promotion_doc(promotion), "settlement_chat")
        SettlementStatusMachine.assert_can_transition(
            from_state=promotion.settlement_status,
            to_state=SettlementStatus.SETTLED.value,
            action="settle_chat",
        )

        problem = check_image_payload(
            data=data,
            mime_type=mime_type,
            filename=filename,
            max_bytes=10 * 1024 * 1024,
            label="结款聊天截图",
        )
        if problem is not None:
            raise SettlementChatScreenshotRequiredError(problem)

        attachment_id: UUID | None = None
        attachment_key: str | None = None
        try:
            attachment, _ = await self._attachment_service.create_upload_record(
                session=self._session,
                tenant_id=user.tenant_id,
                created_by=user.id,
                bucket="private",
                purpose="settlement_chat_screenshot",
                filename=filename,
                mime_type=str(mime_type),
                size_bytes=len(data),
            )
            attachment_id = attachment.id
            attachment_key = attachment.r2_key
            await run_in_threadpool(
                self._attachment_service.upload_bytes,
                data,
                bucket="private",
                key=attachment_key,
                content_type=str(mime_type),
            )
            await self._attachment_service.mark_uploaded(
                session=self._session,
                attachment_id=attachment_id,
                tenant_id=user.tenant_id,
            )

            updated = await self._repo.update_state(
                promotion_id=promotion_id,
                tenant_id=user.tenant_id,
                from_state_field="settlement_status",
                from_state_value=SettlementStatus.PAID.value,
                to_state_value=SettlementStatus.SETTLED.value,
                extra_fields={
                    "settlement_chat_attachment_id": attachment_id,
                    "settled_at": _utcnow(),
                },
            )
            if updated is None:
                raise StateTransitionConflictError(
                    "结款状态已变更，请刷新后重试",
                    details={"promotion_id": str(promotion_id)},
                )

            promotion_state_transitions_total.labels(
                from_state=SettlementStatus.PAID.value,
                to_state=SettlementStatus.SETTLED.value,
                status_field="settlement",
            ).inc()

            # 时间线（6.3）：R9 的 promotion.settled 带聊天截图（附件跟收款码字段规则）
            await LineEventRepository(self._session).append(
                tenant_id=promotion.tenant_id,
                line_id=promotion.line_id,
                event_type=LineEventType.PROMOTION_SETTLED,
                actor_id=user.id,
                promotion_id=promotion_id,
                from_state=SettlementStatus.PAID.value,
                to_state=SettlementStatus.SETTLED.value,
                attachment_id=attachment_id,
            )
            await self._audit.log(
                action="promotion.settlement_chat.upload",
                resource="promotion",
                resource_id=promotion_id,
                before={"settlement_status": SettlementStatus.PAID.value},
                after={
                    "settlement_status": SettlementStatus.SETTLED.value,
                    "attachment_changed": True,
                },
                user_id=user.id,
            )
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            if attachment_key is not None:
                try:
                    await run_in_threadpool(
                        self._attachment_service.delete, "private", attachment_key
                    )
                except Exception:
                    log.warning(
                        "promotion_settlement_chat_compensation_delete_failed",
                        extra={"attachment_id": str(attachment_id)},
                    )
            raise

        return await self._to_response(updated, user)
