"""PromotionService：发布 / 取消 / 品牌评论截图（从 service.py 搬出）。"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.core import events as event_bus
from app.core.attachment import check_image_payload
from app.core.metrics import (
    promotion_state_transitions_total,
)
from app.modules.auth.models import User
from app.modules.promotion.enums import (
    PublishStatus,
    SettlementStatus,
)
from app.modules.promotion.events import (
    PromotionPublished,
)
from app.modules.promotion.exceptions import (
    BrandCommentScreenshotRequiredError,
    CancelReasonRequiredError,
    PromotionNotFoundError,
    StateTransitionConflictError,
)
from app.modules.promotion.schemas import (
    PromotionCancelRequest,
    PromotionPublishRequest,
    PromotionResponse,
)
from app.modules.promotion.service_parts.base import (
    PromotionServiceBase,
    _assert_not_future_publish_date,
    _utcnow,
    log,
)
from app.modules.promotion.state_machines import (
    PublishStatusMachine,
)
from app.modules.urge.enums import UrgeCloseReason


class PromotionPublishMixin(PromotionServiceBase):
    """发布 / 取消 / 品牌评论截图。"""

    # ============================================================
    # 状态推进（6 个）
    # ============================================================

    async def publish(
        self,
        promotion_id: UUID,
        payload: PromotionPublishRequest,
        user: User,
    ) -> PromotionResponse:
        """EP05-S07: 发布（= PRD 说的「提交发布审核」）。"""
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        # 业务前置校验（友好错误）。
        # 顺序要紧：状态机先判。已发布的单再点一次发布，该说「状态不对」而不是
        # 「缺截图」—— 后者会让人去补一张根本不需要的图。
        PublishStatusMachine.assert_can_transition(
            from_state=promotion.publish_status,
            to_state=PublishStatus.PUBLISHED.value,
            action="publish",
        )
        _assert_not_future_publish_date(payload.actual_publish_date)

        # PRD 改动 5：品牌词评论截图在提交发布审核时必传。
        # 后端拦，不只靠前端 —— 和寄拍寄回单号同一个处理方式。
        if promotion.brand_comment_attachment_id is None:
            raise BrandCommentScreenshotRequiredError(
                "提交发布审核前必须上传品牌词评论截图",
                details={"promotion_id": str(promotion_id)},
            )

        # 乐观并发 UPDATE（FB7）
        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="publish_status",
            from_state_value=PublishStatus.UNPUBLISHED.value,
            to_state_value=PublishStatus.PUBLISHED.value,
            extra_fields={
                "publish_url": payload.publish_url,
                "actual_publish_date": payload.actual_publish_date,
            },
        )
        if updated is None:
            raise StateTransitionConflictError(
                "推广状态已变更或已删除，请刷新后重试",
                details={"promotion_id": str(promotion_id)},
            )

        promotion_state_transitions_total.labels(
            from_state=PublishStatus.UNPUBLISHED.value,
            to_state=PublishStatus.PUBLISHED.value,
            status_field="publish",
        ).inc()

        # 同事务推进 settlement_status: 未核查 → 待核查（FB7 跨状态机校验）
        settlement_advanced = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="settlement_status",
            from_state_value=SettlementStatus.NOT_REVIEWED.value,
            to_state_value=SettlementStatus.PENDING_REVIEW.value,
        )
        if settlement_advanced is not None:
            promotion_state_transitions_total.labels(
                from_state=SettlementStatus.NOT_REVIEWED.value,
                to_state=SettlementStatus.PENDING_REVIEW.value,
                status_field="settlement",
            ).inc()

        # 审计
        await self._audit.log(
            action="promotion.publish",
            resource="promotion",
            resource_id=promotion_id,
            after={
                "publish_status": PublishStatus.PUBLISHED.value,
                "publish_url": payload.publish_url,
            },
            user_id=user.id,
        )

        # 通知类事件（无 listener 不抛错）
        published_event = PromotionPublished(
            event_id=uuid4(),
            timestamp=_utcnow(),
            tenant_id=user.tenant_id,
            promotion_id=promotion_id,
            promotion_internal_code=updated.internal_code,
            blogger_id=updated.blogger_id,
            publish_url=payload.publish_url,
            publish_date=payload.actual_publish_date,
            pr_id=user.id,
        )
        try:
            await event_bus.dispatch(published_event, session=self._session)
        except Exception as exc:
            # 通知类事件失败不阻塞主流程；记一条降级 audit
            log.exception("promotion_published_event_dispatch_failed")
            await self._log_event_dispatch_failure(published_event, exc, user, blocking=False)

        # PRD 改动 2：博主确认发布 → 催发任务自动关闭。同事务，不另起一次提交。
        await self._close_urge_task(promotion_id, user, UrgeCloseReason.PUBLISHED)

        await self._session.commit()
        return await self._to_response(updated, user)

    async def cancel(
        self,
        promotion_id: UUID,
        payload: PromotionCancelRequest,
        user: User,
    ) -> PromotionResponse:
        """EP05-S08: 取消（仅 publish_status='未发布' 允许）."""
        if not payload.cancel_reason:
            raise CancelReasonRequiredError("cancel_reason 必填")

        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        PublishStatusMachine.assert_can_transition(
            from_state=promotion.publish_status,
            to_state=PublishStatus.CANCELLED.value,
            action="cancel",
        )

        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="publish_status",
            from_state_value=PublishStatus.UNPUBLISHED.value,
            to_state_value=PublishStatus.CANCELLED.value,
            extra_fields={"cancel_reason": payload.cancel_reason},
        )
        if updated is None:
            raise StateTransitionConflictError(
                "推广状态已变更，请刷新后重试",
                details={"promotion_id": str(promotion_id)},
            )

        promotion_state_transitions_total.labels(
            from_state=PublishStatus.UNPUBLISHED.value,
            to_state=PublishStatus.CANCELLED.value,
            status_field="publish",
        ).inc()

        await self._audit.log(
            action="promotion.cancel",
            resource="promotion",
            resource_id=promotion_id,
            after={"publish_status": PublishStatus.CANCELLED.value},
            user_id=user.id,
        )

        # 单据取消了就别再催了
        await self._close_urge_task(promotion_id, user, UrgeCloseReason.CANCELLED)

        await self._session.commit()
        return await self._to_response(updated, user)

    async def upload_brand_comment(
        self,
        promotion_id: UUID,
        *,
        filename: str | None,
        mime_type: str | None,
        data: bytes,
        user: User,
    ) -> PromotionResponse:
        """上传品牌词评论截图（PRD 改动 5，提交发布审核的前提）。

        不限状态：PR 可能发布前就截好了，也可能被 ``publish`` 挡住之后才来补。
        真正的门槛在 ``publish()`` 里 —— 没有截图就提交不了发布审核。

        后端代传 + 失败补偿删除 R2 对象，骨架与收款码、7 天数据截图一致。
        """
        from fastapi.concurrency import run_in_threadpool

        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        problem = check_image_payload(
            data=data,
            mime_type=mime_type,
            filename=filename,
            max_bytes=10 * 1024 * 1024,
            label="品牌词评论截图",
        )
        if problem is not None:
            raise BrandCommentScreenshotRequiredError(problem)

        attachment_id: UUID | None = None
        attachment_key: str | None = None
        try:
            attachment, _ = await self._attachment_service.create_upload_record(
                session=self._session,
                tenant_id=user.tenant_id,
                created_by=user.id,
                bucket="private",
                purpose="brand_comment_screenshot",
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
            old_id = promotion.brand_comment_attachment_id
            promotion.brand_comment_attachment_id = attachment_id
            await self._audit.log(
                action="promotion.brand_comment.bind",
                resource="promotion",
                resource_id=promotion.id,
                before={"attachment_changed": old_id is not None},
                after={"attachment_changed": True},
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
                        "promotion_brand_comment_compensation_delete_failed",
                        extra={"attachment_id": str(attachment_id)},
                    )
            raise

        return await self._to_response(promotion, user)
