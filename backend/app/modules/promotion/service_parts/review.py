"""PromotionService：审核 / 重提 / 退货单号（从 service.py 搬出）。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.core import events as event_bus
from app.core.metrics import (
    promotion_state_transitions_total,
)
from app.modules.auth.models import User
from app.modules.flow.exceptions import FlowActionForbiddenError
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.flow.matrix import ensure_gates, require
from app.modules.promotion.enums import (
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
    ReviewReasonRequiredError,
    SelfReviewForbiddenError,
    SettlementAlreadyExistsError,
    StateTransitionConflictError,
)
from app.modules.promotion.schemas import (
    PromotionResponse,
    PromotionResubmitRequest,
    PromotionReturnWaybillRequest,
    PromotionReviewRequest,
    ReviewOutcome,
)
from app.modules.promotion.service_parts.base import (
    PromotionServiceBase,
    _assert_not_future_publish_date,
    _utcnow,
)
from app.modules.promotion.state_machines import (
    SettlementStatusMachine,
)

# 结款单（finance）的「已付款」：R2 ⑥ 只认导入的已付款单（推广模块不 import finance）
_SETTLEMENT_PAID = "已付款"


class PromotionReviewMixin(PromotionServiceBase):
    """审核 / 重提 / 退货单号。"""

    async def review(
        self,
        promotion_id: UUID,
        payload: PromotionReviewRequest,
        user: User,
    ) -> PromotionResponse:
        """EP05-S13: PR 主管审核（approve / reject），流程线 3.4 R2 ~ R4。

        校验顺序：矩阵 require（状态机 → 审核权 → 不能审自己谈的单）→ 通过时 ★（寄回单号、收款码）
        → 应付分流：应付 0 → 已结款（R3，写 ``settled_at``）；应付 > 0 → 待付款并同事务发
        SettlementRequested（FB1：required_handler，结款单建在待付款）；已有导入的已付款结款单 →
        已付款、不发事件；已有其他状态的结款单 → 409（R2 ⑥）。响应带 ``review_outcome``。
        事件失败时 audit 脱敏 + 兜底（FB5）。
        review_reason / review_reason_category 只在 reject 时写，approve 不清（7a-4）。
        """
        promotion = await self._writable_promotion(promotion_id, user)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        # ① 状态机（只在待主管审核）② 审核权 ③ 审核人 ≠ 谈款人：矩阵 require（计划 D11）；
        # ★ 缺项只卡通过，所以 gates=False，通过时再 ensure_gates
        actor = await self._flow_actor(user)
        doc = await self._promotion_doc(promotion)
        try:
            require(actor, doc, "review", gates=False)
        except FlowActionForbiddenError as exc:
            if exc.rule == "self_review":
                raise SelfReviewForbiddenError(
                    "不允许自审自己提交的推广",
                    details={"promotion_id": str(promotion_id), "user_id": str(user.id)},
                ) from exc
            raise

        # 自审还按负责 PR 判（R2 ③「≠ pr_id」；矩阵按谈款人，没有谈款才回落负责 PR）
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

        reject_category: str | None = None
        outcome: ReviewOutcome | None = None
        if payload.action == ReviewAction.APPROVE:
            # ④ 寄拍要有寄回单号（或召回成功且有召回寄回截图）⑤ 应付 > 0 要有收款码 → 缺了 422 FLOW_GATE_MISSING
            ensure_gates(actor, doc, "review")
            # 应付分流（3.4）：审核时还没有结款单、追加项为 0，应付就是服务费，与合作模式无关
            if (promotion.quote_amount or Decimal(0)) <= 0:
                # R3：直接已结款，不发事件、不建结款单
                to_state = SettlementStatus.SETTLED.value
                action_name = "approve_no_payment"
                outcome = "settled_directly"
            else:
                # ⑥ 已有结款单就不发事件（不让监听按幂等静默跳过）：导入的已付款单 → 推广单记已付款，其他 → 409
                existing = await self._repo.settlement_brief(
                    tenant_id=promotion.tenant_id, promotion_id=promotion_id
                )
                if existing is None:
                    to_state = SettlementStatus.PENDING_PAYMENT.value
                    action_name = "approve"
                    outcome = "settlement_created"
                elif existing.settlement_status == _SETTLEMENT_PAID:
                    to_state = SettlementStatus.PAID.value
                    action_name = "approve_imported_paid"
                    outcome = "imported_settlement_paid"
                else:
                    raise SettlementAlreadyExistsError(
                        "这张推广单已有结款单，请管理员核对",
                        details={
                            "promotion_id": str(promotion_id),
                            "settlement_id": str(existing.settlement_id),
                            "settlement_no": existing.settlement_no,
                            "settlement_status": existing.settlement_status,
                        },
                    )
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
        if to_state == SettlementStatus.SETTLED.value:
            extra_fields["settled_at"] = now
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

        # 时间线（流程线 6.3）：review.approved（应付、去向）/ rejected（驳回原因、分类）；
        # 写在审计与结款事件之前，后面失败事件跟着回滚（6.4）
        review_payload: dict[str, Any]
        if payload.action == ReviewAction.APPROVE:
            review_event = LineEventType.REVIEW_APPROVED
            review_payload = {"destination": to_state}
            if updated.quote_amount is not None:
                # 金额存字符串（6.3）；读时过服务费字段规则（运营删）
                review_payload["payable"] = str(updated.quote_amount)
        else:
            review_event = LineEventType.REVIEW_REJECTED
            review_payload = {"reject_category": reject_category}
        await LineEventRepository(self._session).append(
            tenant_id=promotion.tenant_id,
            line_id=promotion.line_id,
            event_type=review_event,
            actor_id=user.id,
            promotion_id=promotion_id,
            from_state=SettlementStatus.PENDING_REVIEW.value,
            to_state=to_state,
            note=payload.review_reason if payload.action == ReviewAction.REJECT else None,
            payload=review_payload,
        )
        if outcome == "settled_directly":
            # R3 也算结款闭环（6.3 promotion.settled 的 R3 那条；没有聊天截图）
            await LineEventRepository(self._session).append(
                tenant_id=promotion.tenant_id,
                line_id=promotion.line_id,
                event_type=LineEventType.PROMOTION_SETTLED,
                actor_id=user.id,
                promotion_id=promotion_id,
                from_state=SettlementStatus.PENDING_REVIEW.value,
                to_state=to_state,
            )

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

        # 只有要新建结款单时才发强一致事件（FB1）。应付 0 直接已结款、已有导入的已付款结款单，
        # 都不发 —— 前者发了 finance 会多出金额为 0 的单子，后者会被监听按幂等静默跳过
        if outcome == "settlement_created":
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
                line_id=promotion.line_id,
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

        if outcome == "settled_directly":
            await self.maybe_complete(promotion_id, user)
        await self._session.commit()
        response = await self._to_response(updated, user)
        if outcome is None:
            return response
        return response.model_copy(update={"review_outcome": outcome})

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
        promotion = await self._writable_promotion(promotion_id, user)
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

        # 时间线（流程线 6.3、7.3）：每轮一条 review.resubmitted（7a 的两列只留最近一轮），
        # note = 重提说明，diff 只列这次真改了的发布链接 / 日期
        diff = {
            key: {"before": before[key], "after": after[key]}
            for key in ("publish_url", "actual_publish_date")
            if key in after
        }
        await LineEventRepository(self._session).append(
            tenant_id=promotion.tenant_id,
            line_id=promotion.line_id,
            event_type=LineEventType.REVIEW_RESUBMITTED,
            actor_id=user.id,
            promotion_id=promotion_id,
            from_state=SettlementStatus.REJECTED.value,
            to_state=SettlementStatus.PENDING_REVIEW.value,
            note=payload.note,
            payload={"diff": diff} if diff else None,
        )

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
        promotion = await self._writable_promotion(promotion_id, user)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        before = promotion.return_waybill
        promotion.return_waybill = payload.return_waybill
        await self._session.flush()
        if payload.return_waybill != before:
            # 时间线（流程线 6.3）：同一单号重复提交不再记
            await LineEventRepository(self._session).append(
                tenant_id=promotion.tenant_id,
                line_id=promotion.line_id,
                event_type=LineEventType.PROMOTION_RETURN_WAYBILL_SET,
                actor_id=user.id,
                promotion_id=promotion_id,
                payload={"return_waybill": payload.return_waybill},
            )
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
