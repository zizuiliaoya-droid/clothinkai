"""PromotionService：数据录入 / 复盘 / 点赞回写（从 service.py 搬出）。"""

from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID, uuid4

from app.core.attachment import check_image_payload
from app.core.metrics import (
    promotion_state_transitions_total,
)
from app.modules.auth.models import User
from app.modules.promotion.enums import (
    RetroStatus,
    SettlementStatus,
)
from app.modules.promotion.exceptions import (
    MetricsScreenshotRequiredError,
    PromotionNotFoundError,
    RetroContentMissingError,
    RetroSelfConfirmForbiddenError,
    SettlementNotPaidError,
    StateTransitionConflictError,
)
from app.modules.promotion.models import (
    BloggerRetrospective,
    Promotion,
)
from app.modules.promotion.schemas import (
    PromotionMetricsRequest,
    PromotionResponse,
    RetrospectiveConfirmRequest,
    RetrospectiveResponse,
    RetrospectiveSubmitRequest,
)
from app.modules.promotion.service_parts.base import (
    PromotionServiceBase,
    _utcnow,
    log,
)
from app.modules.promotion.state_machines import (
    RetroStatusMachine,
)


class PromotionRetroMixin(PromotionServiceBase):
    """数据录入 / 复盘 / 点赞回写。"""

    # ============================================================
    # 复盘（PRD V1.4 改动 4）
    # ============================================================

    async def record_metrics(
        self,
        promotion_id: UUID,
        payload: PromotionMetricsRequest,
        user: User,
        *,
        screenshot: tuple[str | None, str | None, bytes] | None = None,
    ) -> PromotionResponse:
        """录发布满 7 天的数据，推进到「待复盘」。

        PRD 原文「已结款 → 发布满 7 天，PR 录入点赞/收藏/评论 + 截图 → 待复盘」。
        三个指标 + 截图都必填，截图走后端代传（与收款码同一套骨架）。

        跨状态机前置：``settlement_status = '已付款'``。结款没完成就复盘没有意义 ——
        ROI 的分母都还没定。

        指标与状态推进在**同一个事务**：不然会出现「数字录进去了但状态没动」，
        之后谁也不知道该不该补一次。
        """
        from fastapi.concurrency import run_in_threadpool

        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        if promotion.settlement_status != SettlementStatus.PAID.value:
            raise SettlementNotPaidError(
                "只有已结款的推广单可以录 7 天数据",
                details={
                    "promotion_id": str(promotion_id),
                    "settlement_status": promotion.settlement_status,
                },
            )
        if screenshot is None:
            raise MetricsScreenshotRequiredError("录 7 天数据必须上传数据截图")

        filename, mime_type, data = screenshot
        problem = check_image_payload(
            data=data,
            mime_type=mime_type,
            filename=filename,
            max_bytes=10 * 1024 * 1024,
            label="数据截图",
        )
        if problem is not None:
            raise MetricsScreenshotRequiredError(problem)

        RetroStatusMachine.assert_can_transition(
            from_state=promotion.retro_status,
            to_state=RetroStatus.PENDING_RETRO.value,
            action="record_metrics",
        )

        attachment_id: UUID | None = None
        attachment_key: str | None = None
        try:
            attachment, _ = await self._attachment_service.create_upload_record(
                session=self._session,
                tenant_id=user.tenant_id,
                created_by=user.id,
                bucket="private",
                purpose="promotion_metrics",
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

            now = _utcnow()
            updated = await self._repo.update_state(
                promotion_id=promotion_id,
                tenant_id=user.tenant_id,
                from_state_field="retro_status",
                from_state_value=RetroStatus.NOT_STARTED.value,
                to_state_value=RetroStatus.PENDING_RETRO.value,
                extra_fields={
                    "like_count": payload.like_count,
                    "collect_count": payload.collect_count,
                    "comment_count": payload.comment_count,
                    "metrics_attachment_id": attachment_id,
                    "metrics_recorded_at": now,
                },
            )
            if updated is None:
                raise StateTransitionConflictError(
                    "复盘状态已变更，请刷新后重试",
                    details={"promotion_id": str(promotion_id)},
                )

            promotion_state_transitions_total.labels(
                from_state=RetroStatus.NOT_STARTED.value,
                to_state=RetroStatus.PENDING_RETRO.value,
                status_field="retro",
            ).inc()

            await self._audit.log(
                action="promotion.record_metrics",
                resource="promotion",
                resource_id=promotion_id,
                after={
                    "like_count": payload.like_count,
                    "collect_count": payload.collect_count,
                    "comment_count": payload.comment_count,
                    "retro_status": RetroStatus.PENDING_RETRO.value,
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
                        "promotion_metrics_compensation_delete_failed",
                        extra={"attachment_id": str(attachment_id)},
                    )
            raise

        return await self._to_response(updated, user)

    async def submit_retrospective(
        self,
        promotion_id: UUID,
        payload: RetrospectiveSubmitRequest,
        user: User,
    ) -> PromotionResponse:
        """PR 提交复盘文字，推进到「待确认」。

        文字写进 ``blogger_retrospective`` 子表而不是 promotion 字段：PRD 要求
        「永久写入博主档案、不随单据关闭而丢失」，而被打回重写时字段会被覆盖。
        子表里追加新行，旧版留着。
        """
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        RetroStatusMachine.assert_can_transition(
            from_state=promotion.retro_status,
            to_state=RetroStatus.PENDING_CONFIRM.value,
            action="submit_retro",
        )

        self._repo.add_retrospective(
            BloggerRetrospective(
                id=uuid4(),
                tenant_id=user.tenant_id,
                blogger_id=promotion.blogger_id,
                promotion_id=promotion_id,
                content=payload.content,
                created_by=user.id,
            )
        )
        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="retro_status",
            from_state_value=RetroStatus.PENDING_RETRO.value,
            to_state_value=RetroStatus.PENDING_CONFIRM.value,
        )
        if updated is None:
            raise StateTransitionConflictError(
                "复盘状态已变更，请刷新后重试",
                details={"promotion_id": str(promotion_id)},
            )

        promotion_state_transitions_total.labels(
            from_state=RetroStatus.PENDING_RETRO.value,
            to_state=RetroStatus.PENDING_CONFIRM.value,
            status_field="retro",
        ).inc()

        await self._audit.log(
            action="promotion.submit_retro",
            resource="promotion",
            resource_id=promotion_id,
            after={"retro_status": RetroStatus.PENDING_CONFIRM.value},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user)

    async def confirm_retrospective(
        self,
        promotion_id: UUID,
        payload: RetrospectiveConfirmRequest,
        user: User,
    ) -> PromotionResponse:
        """主管确认复盘（→ 已完成）或打回（→ 待复盘）。

        确认时给子表那条记录盖上 ``confirmed_by/confirmed_at`` —— hover 卡只展示
        已确认的复盘，没过主管的是草稿，不该进博主档案误导下次选博主的人。

        打回**不删**旧记录：PR 重写时追加一条，被打回的那版留着看得见演变过程。
        """
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        retro = await self._repo.latest_retrospective(
            tenant_id=user.tenant_id, promotion_id=promotion_id
        )
        if retro is None:
            raise RetroContentMissingError(
                "找不到复盘内容，无法确认",
                details={"promotion_id": str(promotion_id)},
            )
        # 自己写的复盘自己批，主管这道关就没有意义。
        # 必须在这里挡 —— promotion.retro:confirm 的一级域是 promotion，
        # PR 的 promotion.*:* 会被通配命中，权限层拦不住。
        if retro.created_by is not None and retro.created_by == user.id:
            raise RetroSelfConfirmForbiddenError(
                "不允许确认自己写的复盘",
                details={"promotion_id": str(promotion_id), "user_id": str(user.id)},
            )

        now = _utcnow()
        if payload.approve:
            to_state = RetroStatus.COMPLETED.value
            action_name = "confirm_retro"
            extra: dict[str, Any] = {
                "retro_confirmed_by": user.id,
                "retro_confirmed_at": now,
            }
        else:
            to_state = RetroStatus.PENDING_RETRO.value
            action_name = "reject_retro"
            extra = {}

        RetroStatusMachine.assert_can_transition(
            from_state=promotion.retro_status,
            to_state=to_state,
            action=action_name,
        )
        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="retro_status",
            from_state_value=RetroStatus.PENDING_CONFIRM.value,
            to_state_value=to_state,
            extra_fields=extra,
        )
        if updated is None:
            raise StateTransitionConflictError(
                "复盘状态已变更，请刷新后重试",
                details={"promotion_id": str(promotion_id)},
            )

        if payload.approve:
            # 盖确认戳，这条复盘才会出现在博主档案里
            retro.confirmed_by = user.id
            retro.confirmed_at = now
            await self._session.flush()

        promotion_state_transitions_total.labels(
            from_state=RetroStatus.PENDING_CONFIRM.value,
            to_state=to_state,
            status_field="retro",
        ).inc()

        await self._audit.log(
            action=f"promotion.{action_name}",
            resource="promotion",
            resource_id=promotion_id,
            after={"retro_status": to_state, "opinion": payload.opinion},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user)

    async def blogger_retrospectives(
        self, blogger_id: UUID, user: User, *, limit: int = 20
    ) -> builtins.list[RetrospectiveResponse]:
        """某博主的历史复盘，倒序（PRD：hover 卡展示该博主所有历史复盘）。"""
        rows = await self._repo.blogger_retrospectives(
            tenant_id=user.tenant_id, blogger_id=blogger_id, limit=limit
        )
        return [RetrospectiveResponse(**r) for r in rows]

    async def update_like_count(
        self,
        *,
        promotion_id: UUID,
        like_count: int,
        tenant_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> Promotion:
        """U13 数据采集 Worker 内部调用：更新 like_count.

        不暴露 HTTP（只在 Worker 通过内部认证调用）。
        """
        updated = await self._repo.update_like_count(
            promotion_id=promotion_id,
            tenant_id=tenant_id,
            like_count=like_count,
        )
        if updated is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在或已停用")

        await self._audit.log(
            action="promotion.update_like_count",
            resource="promotion",
            resource_id=promotion_id,
            after={"like_count": like_count},
            user_id=actor_user_id,
            actor_type="system",
        )
        await self._session.commit()
        return updated
