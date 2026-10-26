"""PromotionService：数据录入 / 复盘 / 点赞回写（从 service.py 搬出）。"""

from __future__ import annotations

import builtins
from typing import Any
from uuid import UUID, uuid4

from app.core.attachment import check_image_payload
from app.core.exceptions import IllegalStateTransitionError
from app.core.metrics import (
    promotion_state_transitions_total,
)
from app.modules.auth.models import User
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.flow.matrix import require
from app.modules.promotion.enums import (
    RetroStatus,
    SettlementStatus,
)
from app.modules.promotion.exceptions import (
    MetricsNotAvailableYetError,
    MetricsScreenshotRequiredError,
    PromotionNotFoundError,
    StateTransitionConflictError,
)
from app.modules.promotion.models import (
    BloggerRetrospective,
    Promotion,
)
from app.modules.promotion.schemas import (
    PromotionMetricsRequest,
    PromotionResponse,
    RetrospectiveResponse,
    RetrospectiveWriteRequest,
)
from app.modules.promotion.service_parts.base import (
    PromotionServiceBase,
    _utcnow,
    log,
)
from app.modules.promotion.stage_calculator import metrics_available_from, seven_days_reached
from app.modules.promotion.state_machines import (
    RetroStatusMachine,
)
from app.modules.promotion.urge_calculator import get_today


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
        """录 7 天数据（流程线 3.7，7d-11）：已结款且发文满 7 天，只录一次。

        校验顺序（3.7）：未录过（录过 → 422 ``ILLEGAL_STATE_TRANSITION``）→ 已结款（否则 422
        ``METRICS_NOT_AVAILABLE_YET``，``blocked_by = settlement``）→ ``get_today() ≥ 发布日 + 7``（北京时间；
        否则同码，``blocked_by = seven_days``，提示可录日期；没有发布日不拦）→ 矩阵 ``require``（阶段 + 权限）
        → 三个指标 + 截图（截图后端代传）→ ``record_metrics_once`` 条件 UPDATE（``metrics_recorded_at IS NULL``，
        0 行 → 409）。指标、截图与时间线同一事务，任何一步失败都回滚并删掉已传的对象。

        录数与复盘正交，不推进复盘状态。
        """
        from fastapi.concurrency import run_in_threadpool

        # 锁前捕获“未录过”：并发等待期间被录入应报409，串行重复仍按既有422。
        snapshot = await self._repo.get_by_id(promotion_id)
        was_unrecorded = snapshot is not None and snapshot.metrics_recorded_at is None
        promotion = await self._writable_promotion(promotion_id, user)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        if promotion.metrics_recorded_at is not None:
            if was_unrecorded:
                raise StateTransitionConflictError("7 天数据已被录入，请刷新后重试")
            raise IllegalStateTransitionError(
                "7 天数据已经录过，只能录一次",
                details={"promotion_id": str(promotion_id), "action": "metrics"},
            )
        today = get_today()
        available_from = metrics_available_from(promotion.actual_publish_date)
        gate_details: dict[str, Any] = {
            "promotion_id": str(promotion_id),
            "available_from": available_from.isoformat() if available_from else None,
        }
        if promotion.settlement_status != SettlementStatus.SETTLED.value:
            raise MetricsNotAvailableYetError(
                "已结款后可录 7 天数据",
                details={
                    **gate_details,
                    "blocked_by": "settlement",
                    "settlement_status": promotion.settlement_status,
                },
            )
        if available_from is not None and not seven_days_reached(
            promotion.actual_publish_date, today
        ):
            raise MetricsNotAvailableYetError(
                f"{available_from.isoformat()} 起可以录 7 天数据",
                details={**gate_details, "blocked_by": "seven_days"},
            )
        actor = await self._flow_actor(user)
        stage = await self._compute_stage(promotion, today=today)
        require(actor, await self._promotion_doc(promotion, stage=stage), "metrics")

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

        from_retro = promotion.retro_status

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

            updated = await self._repo.record_metrics_once(
                promotion_id=promotion_id,
                tenant_id=user.tenant_id,
                values={
                    "like_count": payload.like_count,
                    "collect_count": payload.collect_count,
                    "comment_count": payload.comment_count,
                    "metrics_attachment_id": attachment_id,
                },
            )
            if updated is None:
                raise StateTransitionConflictError(
                    "7 天数据已被录入，请刷新后重试",
                    details={"promotion_id": str(promotion_id)},
                )
            to_retro = updated.retro_status

            if to_retro != from_retro:
                promotion_state_transitions_total.labels(
                    from_state=from_retro,
                    to_state=to_retro,
                    status_field="retro",
                ).inc()

            # 时间线（流程线 6.3）：promotion.metrics_recorded，附件 = 7 天数据截图；
            # 在 try 里：写失败整笔回滚、截图照删
            await LineEventRepository(self._session).append(
                tenant_id=updated.tenant_id,
                line_id=updated.line_id,
                event_type=LineEventType.PROMOTION_METRICS_RECORDED,
                actor_id=user.id,
                promotion_id=promotion_id,
                from_state=from_retro,
                to_state=to_retro,
                attachment_id=attachment_id,
                payload={
                    "like_count": payload.like_count,
                    "collect_count": payload.collect_count,
                    "comment_count": payload.comment_count,
                },
            )

            await self._audit.log(
                action="promotion.record_metrics",
                resource="promotion",
                resource_id=promotion_id,
                after={
                    "like_count": payload.like_count,
                    "collect_count": payload.collect_count,
                    "comment_count": payload.comment_count,
                    "retro_status": to_retro,
                },
                user_id=user.id,
            )
            await self.maybe_complete(promotion_id, user)
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

    async def write_retrospective(
        self,
        promotion_id: UUID,
        payload: RetrospectiveWriteRequest,
        user: User,
    ) -> PromotionResponse:
        """主管写即完成；持真正*者可追加完成版，旧稿和旧确认版永久留档。"""
        from app.core.exceptions import PermissionDeniedError
        from app.modules.promotion.enums import PublishStatus

        promotion = await self._writable_promotion(promotion_id, user)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        actor = await self._flow_actor(user)
        if not actor.perms.has("promotion_retro", "write"):
            raise PermissionDeniedError("缺少主管写复盘权限")
        revising = promotion.retro_status == RetroStatus.COMPLETED.value
        if revising and not actor.is_star:
            raise IllegalStateTransitionError("已完成的复盘仅管理员可修改")
        if promotion.publish_status != PublishStatus.PUBLISHED.value:
            raise IllegalStateTransitionError("已发布后才可写复盘")
        action = "revise_retro" if revising else "write_retro"
        RetroStatusMachine.assert_can_transition(
            promotion.retro_status, RetroStatus.COMPLETED.value, action
        )
        before = promotion.retro_status
        now, retrospective_id = _utcnow(), uuid4()
        self._repo.add_retrospective(
            BloggerRetrospective(
                id=retrospective_id,
                tenant_id=user.tenant_id,
                blogger_id=promotion.blogger_id,
                promotion_id=promotion_id,
                content=payload.content,
                created_by=user.id,
                confirmed_by=user.id,
                confirmed_at=now,
                created_at=now,
            )
        )
        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="retro_status",
            from_state_value=before,
            to_state_value=RetroStatus.COMPLETED.value,
            extra_fields={"retro_confirmed_by": user.id, "retro_confirmed_at": now},
        )
        if updated is None:
            raise StateTransitionConflictError("复盘状态已变更，请刷新后重试")
        await LineEventRepository(self._session).append(
            tenant_id=updated.tenant_id,
            line_id=updated.line_id,
            event_type=LineEventType.RETRO_EDITED if revising else LineEventType.RETRO_WRITTEN,
            actor_id=user.id,
            promotion_id=promotion_id,
            from_state=before,
            to_state=RetroStatus.COMPLETED.value,
            note=payload.content,
            payload={"retrospective_id": str(retrospective_id)},
        )
        await self._audit.log(
            action=f"promotion.{action}",
            resource="promotion",
            resource_id=promotion_id,
            after={"retro_status": RetroStatus.COMPLETED.value},
            user_id=user.id,
        )
        await self.maybe_complete(promotion_id, user)
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
