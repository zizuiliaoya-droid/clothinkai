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
    RetroContentMissingError,
    RetroSelfConfirmForbiddenError,
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

        过渡期（PR-7 前复盘仍是旧流程）：同一条 UPDATE 把复盘 未开始 → 待复盘。完结检查随 PR-7。
        """
        from fastapi.concurrency import run_in_threadpool

        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        if promotion.metrics_recorded_at is not None:
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

        retrospective_id = uuid4()
        self._repo.add_retrospective(
            BloggerRetrospective(
                id=retrospective_id,
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

        # 时间线（流程线 6.3）：retro.written，note = 复盘全文；打回重写再追加一条（子表也是追加）
        await LineEventRepository(self._session).append(
            tenant_id=promotion.tenant_id,
            line_id=promotion.line_id,
            event_type=LineEventType.RETRO_WRITTEN,
            actor_id=user.id,
            promotion_id=promotion_id,
            from_state=RetroStatus.PENDING_RETRO.value,
            to_state=RetroStatus.PENDING_CONFIRM.value,
            note=payload.content,
            payload={"retrospective_id": str(retrospective_id)},
        )

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

        # 时间线（计划 D12）：旧复盘的确认 / 打回，note = 主管意见；PR-7 复盘 3 态之后不再写
        await LineEventRepository(self._session).append(
            tenant_id=promotion.tenant_id,
            line_id=promotion.line_id,
            event_type=(
                LineEventType.RETRO_CONFIRMED if payload.approve else LineEventType.RETRO_RETURNED
            ),
            actor_id=user.id,
            promotion_id=promotion_id,
            from_state=RetroStatus.PENDING_CONFIRM.value,
            to_state=to_state,
            note=payload.opinion or None,
            payload={"retrospective_id": str(retro.id)},
        )

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
