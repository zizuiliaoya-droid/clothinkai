"""PromotionService：召回：发起 / 成功 / 失败（从 service.py 搬出）。"""

from __future__ import annotations

from uuid import UUID

from app.core.metrics import (
    promotion_state_transitions_total,
)
from app.modules.auth.models import User
from app.modules.promotion.enums import (
    PublishStatus,
    RecallStatus,
)
from app.modules.promotion.exceptions import (
    PromotionNotFoundError,
    StateTransitionConflictError,
)
from app.modules.promotion.schemas import (
    PromotionRecallStartRequest,
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

    async def start_recall(
        self,
        promotion_id: UUID,
        payload: PromotionRecallStartRequest,
        user: User,
    ) -> PromotionResponse:
        """EP05-S09: 启动召回（跨状态机：要求 publish_status ∈ {已发布, 已取消}）."""
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        # BR-U04-24 跨状态机校验
        if promotion.publish_status not in (
            PublishStatus.PUBLISHED.value,
            PublishStatus.CANCELLED.value,
        ):
            raise StateTransitionConflictError(
                "仅「已发布」或「已取消」状态可启动召回",
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

        await self._audit.log(
            action="promotion.start_recall",
            resource="promotion",
            resource_id=promotion_id,
            after={"recall_status": RecallStatus.RECALLING.value},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user)

    async def recall_success(self, promotion_id: UUID, user: User) -> PromotionResponse:
        """EP05-S09: 召回成功（终态）."""
        return await self._recall_finish(
            promotion_id=promotion_id,
            user=user,
            to_state=RecallStatus.RECALLED_SUCCESS.value,
            action_log="promotion.recall_success",
        )

    async def recall_failure(self, promotion_id: UUID, user: User) -> PromotionResponse:
        """EP05-S09: 召回失败（可重试）."""
        return await self._recall_finish(
            promotion_id=promotion_id,
            user=user,
            to_state=RecallStatus.RECALLED_FAILURE.value,
            action_log="promotion.recall_failure",
        )

    async def _recall_finish(
        self,
        *,
        promotion_id: UUID,
        user: User,
        to_state: str,
        action_log: str,
    ) -> PromotionResponse:
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

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

        promotion_state_transitions_total.labels(
            from_state=RecallStatus.RECALLING.value,
            to_state=to_state,
            status_field="recall",
        ).inc()

        await self._audit.log(
            action=action_log,
            resource="promotion",
            resource_id=promotion_id,
            after={"recall_status": to_state},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user)
