"""PromotionService：审核 / 重提 / 退货单号（从 service.py 搬出）。"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.core import events as event_bus
from app.core.metrics import (
    promotion_state_transitions_total,
)
from app.modules.auth.models import User
from app.modules.promotion.enums import (
    CooperationMode,
    PublishStatus,
    ReviewAction,
    SettlementStatus,
)
from app.modules.promotion.events import (
    SettlementRequested,
)
from app.modules.promotion.exceptions import (
    PromotionNotFoundError,
    RejectReasonCategoryRequiredError,
    ReturnWaybillRequiredError,
    ReviewReasonRequiredError,
    SelfReviewForbiddenError,
    StateTransitionConflictError,
)
from app.modules.promotion.schemas import (
    PromotionResponse,
    PromotionResubmitRequest,
    PromotionReturnWaybillRequest,
    PromotionReviewRequest,
)
from app.modules.promotion.service_parts.base import (
    PromotionServiceBase,
    _assert_not_future_publish_date,
    _utcnow,
)
from app.modules.promotion.state_machines import (
    SettlementStatusMachine,
)


class PromotionReviewMixin(PromotionServiceBase):
    """审核 / 重提 / 退货单号。"""

    async def review(
        self,
        promotion_id: UUID,
        payload: PromotionReviewRequest,
        user: User,
    ) -> PromotionResponse:
        """EP05-S13: PR 主管审核（approve / reject）.

        approve 时同事务发 SettlementRequested 事件（FB1：required_handler）。
        失败时 audit 脱敏 + 兜底（FB5）。
        review_reason / review_reason_category 只在 reject 时写，approve 不清（7a-4）。
        """
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        # 自审禁止
        if promotion.pr_id is not None and promotion.pr_id == user.id:
            raise SelfReviewForbiddenError(
                "不允许自审自己提交的推广",
                details={"promotion_id": str(promotion_id), "user_id": str(user.id)},
            )

        # 跨状态机校验：approve 前 publish_status 必须 = 已发布
        if (
            payload.action == ReviewAction.APPROVE
            and promotion.publish_status != PublishStatus.PUBLISHED.value
        ):
            raise StateTransitionConflictError(
                "仅「已发布」状态的推广可审核通过",
                details={"publish_status": promotion.publish_status},
            )

        is_barter = promotion.cooperation_mode == CooperationMode.BARTER.value
        reject_category: str | None = None
        if payload.action == ReviewAction.APPROVE:
            # 寄拍硬门槛：没有博主寄回衣服单号不许往财务走。
            # PRD 原文「不上传单号财务看不到单据，禁止结款」，且明确要求后端校验。
            if (
                promotion.cooperation_mode == CooperationMode.CONSIGNMENT.value
                and not (promotion.return_waybill or "").strip()
            ):
                raise ReturnWaybillRequiredError(
                    "寄拍模式需要先上传博主寄回衣服单号才能通过审核",
                    details={
                        "promotion_id": str(promotion_id),
                        "cooperation_mode": promotion.cooperation_mode,
                    },
                )
            if is_barter:
                # 置换没有博主服务费，审核通过即结清，跳过待付款与财务付款
                to_state = SettlementStatus.PAID.value
                action_name = "approve_barter"
            else:
                to_state = SettlementStatus.PENDING_PAYMENT.value
                action_name = "approve"
        else:  # REJECT
            if not payload.review_reason:
                raise ReviewReasonRequiredError("驳回时 review_reason 必填")
            if payload.review_reason_category is None:
                raise RejectReasonCategoryRequiredError(
                    "驳回时必须选择原因分类（延迟发文 / 流量差补发 / 衣服未寄回）"
                )
            reject_category = payload.review_reason_category.value
            to_state = SettlementStatus.REJECTED.value
            action_name = "reject"

        SettlementStatusMachine.assert_can_transition(
            from_state=promotion.settlement_status,
            to_state=to_state,
            action=action_name,
        )

        now = _utcnow()
        extra_fields: dict[str, Any] = {
            "reviewed_by": user.id,
            "reviewed_at": now,
            "review_action": payload.action.value,
        }
        if payload.action == ReviewAction.REJECT:
            # 驳回说明与分类只在驳回时写，通过时不动：这两列表示「最近一次驳回」。
            # 驳回 → 重新提交（7a-4）→ 通过之后，结款环节仍要看得到上一轮为什么驳；
            # 从没驳回过的单这两列本来就是 NULL，通过时不受影响
            extra_fields["review_reason"] = payload.review_reason
            extra_fields["review_reason_category"] = reject_category
        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="settlement_status",
            from_state_value=SettlementStatus.PENDING_REVIEW.value,
            to_state_value=to_state,
            extra_fields=extra_fields,
        )
        if updated is None:
            raise StateTransitionConflictError(
                "结款状态已变更，请刷新后重试",
                details={"promotion_id": str(promotion_id)},
            )

        promotion_state_transitions_total.labels(
            from_state=SettlementStatus.PENDING_REVIEW.value,
            to_state=to_state,
            status_field="settlement",
        ).inc()

        await self._audit.log(
            action=f"promotion.review.{payload.action.value}",
            resource="promotion",
            resource_id=promotion_id,
            after={
                "settlement_status": to_state,
                "review_action": payload.action.value,
                "cooperation_mode": promotion.cooperation_mode,
                "review_reason_category": reject_category,
            },
            user_id=user.id,
        )

        # 审核通过且需要付款时才发强一致事件（FB1）。
        # 置换直接到已付款，不建结款单 —— 发了 finance 会多出一堆金额为 0 的单子。
        if payload.action == ReviewAction.APPROVE and not is_barter:
            event = SettlementRequested(
                event_id=uuid4(),
                timestamp=now,
                tenant_id=user.tenant_id,
                promotion_id=promotion_id,
                promotion_internal_code=updated.internal_code,
                blogger_id=updated.blogger_id,
                style_id=updated.style_id,
                amount=updated.quote_amount,
                pr_id=updated.pr_id,
                requested_by=user.id,
                requested_at=now,
            )
            try:
                await event_bus.dispatch(event, session=self._session)
            except Exception as exc:
                # 强一致事件失败：脱敏 audit + 重新抛出（事务回滚）
                try:
                    import sentry_sdk

                    sentry_sdk.capture_exception(exc)
                except Exception:  # noqa: S110 Sentry 上报是 best-effort，其失败不得掩盖原始异常（下方 raise 会保留）
                    pass
                await self._log_event_dispatch_failure(event, exc, user, blocking=True)
                raise

        await self._session.commit()
        return await self._to_response(updated, user)

    async def resubmit(
        self,
        promotion_id: UUID,
        payload: PromotionResubmitRequest,
        user: User,
    ) -> PromotionResponse:
        """7a-4 驳回后重新提交：已驳回 → 待核查。

        - 只允许从「已驳回」出发，且 publish_status 必须是「已发布」（否则进了待核查也批不了，
          与 review approve 的跨状态机校验同理）
        - 状态机先判，再判日期（plan D4）
        - 可同改发布链接 / 实际发布日期：不传不动；传了且与现值不同才写入、才进 audit
        - 上一轮 reviewed_* / review_action / review_reason / review_reason_category **不清**，
          主管再审时能看到上次为什么驳（再审通过也不清驳回原因，见 ``review``）；
          resubmit_note / resubmitted_at 每轮覆盖
        """
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        SettlementStatusMachine.assert_can_transition(
            from_state=promotion.settlement_status,
            to_state=SettlementStatus.PENDING_REVIEW.value,
            action="resubmit",
        )
        if promotion.publish_status != PublishStatus.PUBLISHED.value:
            raise StateTransitionConflictError(
                "仅「已发布」状态的推广可重新提交审核",
                details={"publish_status": promotion.publish_status},
            )
        _assert_not_future_publish_date(payload.actual_publish_date)

        before: dict[str, Any] = {"settlement_status": SettlementStatus.REJECTED.value}
        after: dict[str, Any] = {"settlement_status": SettlementStatus.PENDING_REVIEW.value}
        extra: dict[str, Any] = {"resubmit_note": payload.note, "resubmitted_at": _utcnow()}
        if payload.publish_url is not None and payload.publish_url != promotion.publish_url:
            extra["publish_url"] = payload.publish_url
            before["publish_url"] = promotion.publish_url
            after["publish_url"] = payload.publish_url
        if (
            payload.actual_publish_date is not None
            and payload.actual_publish_date != promotion.actual_publish_date
        ):
            extra["actual_publish_date"] = payload.actual_publish_date
            before["actual_publish_date"] = (
                promotion.actual_publish_date.isoformat()
                if promotion.actual_publish_date is not None
                else None
            )
            after["actual_publish_date"] = payload.actual_publish_date.isoformat()
        after["has_note"] = True

        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="settlement_status",
            from_state_value=SettlementStatus.REJECTED.value,
            to_state_value=SettlementStatus.PENDING_REVIEW.value,
            extra_fields=extra,
        )
        if updated is None:
            raise StateTransitionConflictError(
                "结款状态已变更，请刷新后重试",
                details={"promotion_id": str(promotion_id)},
            )

        promotion_state_transitions_total.labels(
            from_state=SettlementStatus.REJECTED.value,
            to_state=SettlementStatus.PENDING_REVIEW.value,
            status_field="settlement",
        ).inc()

        await self._audit.log(
            action="promotion.resubmit",
            resource="promotion",
            resource_id=promotion_id,
            before=before,
            after=after,
            user_id=user.id,
        )

        await self._session.commit()
        return await self._to_response(updated, user)

    async def set_return_waybill(
        self,
        promotion_id: UUID,
        payload: PromotionReturnWaybillRequest,
        user: User,
    ) -> PromotionResponse:
        """上传博主寄回衣服单号（寄拍模式审核通过的前提）。

        不限制状态：PR 可能在发货后就拿到了单号，也可能审核被拦住之后才补。
        真正的门槛在 ``review()`` 里 —— 没有单号就通不过审核。
        """
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        before = promotion.return_waybill
        promotion.return_waybill = payload.return_waybill
        await self._session.flush()
        await self._audit.log(
            action="promotion.return_waybill.update",
            resource="promotion",
            resource_id=promotion_id,
            before={"return_waybill_present": bool(before)},
            after={"return_waybill_present": True},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(promotion, user)
