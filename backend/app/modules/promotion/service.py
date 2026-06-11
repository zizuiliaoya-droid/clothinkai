"""U04 promotion 服务层（PromotionService）。

按 nfr-design/logical-components.md §4.1 + nfr-design-patterns.md §2-§5：

业务编排层：
- CRUD：create_promotion / update_promotion / get_promotion / list_promotions / soft_delete_promotion
- 状态推进（6 个）：publish / cancel / start_recall / recall_success / recall_failure / review
- 内部 API：update_like_count（U13 数据采集 Worker 调用）

关键设计：
- 状态机推进通过 ``repository.update_state``（FB7：UPDATE WHERE old_state RETURNING）
- review approve 同事务发 SettlementRequested 事件（FB1：required_handler）
- 失败 audit 脱敏 + 兜底（FB5）
- 字段写权限硬编码（待 U09 清理）
- 衍生字段实时计算（urge_status / dual_platform / effective_like_count / is_hit / cpl）
- match 降级语义：业务未匹配 → 200 + 空数组；系统失败 → 异常自然冒泡 → 5xx + Sentry
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events as event_bus
from app.core.audit import AuditService
from app.core.db import AsyncSessionBypass
from app.core.metrics import (
    promotion_search_results_count,
    promotion_state_transitions_total,
)
from app.core.tenancy import bypass_rls_ctx, request_id_ctx
from app.core.security.field_permissions import (
    build_field_perm_context,
    can_read_field,
    can_write_field,
)
from app.modules.auth.models import Tenant, User
from app.modules.auth.repository import PermissionRepository, RoleRepository
from app.modules.blogger.repository import BloggerRepository
from app.modules.product.models import Sku
from app.modules.product.repository import SkuRepository, StyleRepository

from app.modules.promotion.domain import (
    build_promotion_audit_changes,
    compute_promotion_changes,
    format_internal_code,
)
from app.modules.promotion.enums import (
    PublishStatus,
    RecallStatus,
    ReviewAction,
    SettlementStatus,
)
from app.modules.promotion.events import (
    PromotionPublished,
    SettlementRequested,
)
from app.modules.promotion.exceptions import (
    CancelReasonRequiredError,
    FieldPermissionDenied,
    InvalidBloggerReferenceError,
    InvalidSkuReferenceError,
    InvalidStyleReferenceError,
    PromotionNotFoundError,
    PublishUrlRequiredError,
    ReviewReasonRequiredError,
    SelfReviewForbiddenError,
    StateTransitionConflictError,
)
from app.modules.promotion.legacy_settings import (
    HIT_THRESHOLD_LIKE_COUNT,
    IMPORTANT_THRESHOLD_DAYS,
    URGE_THRESHOLD_DAYS,
)
from app.modules.promotion.metrics_calculator import (
    calculate_cpl,
    calculate_effective_like_count,
    calculate_is_hit,
)
from app.modules.promotion.models import Promotion
from app.modules.promotion.repository import (
    PromotionListFilters as RepoPromotionListFilters,
)
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.schemas import (
    PromotionCancelRequest,
    PromotionCreate,
    PromotionDuplicateWarning,
    PromotionListFilters as ApiPromotionListFilters,
    PromotionPage,
    PromotionPublishRequest,
    PromotionRecallResultRequest,
    PromotionRecallStartRequest,
    PromotionResponse,
    PromotionReviewRequest,
    PromotionUpdate,
)
from app.modules.promotion.state_machines import (
    PublishStatusMachine,
    RecallStatusMachine,
    SettlementStatusMachine,
)
from app.modules.promotion.urge_calculator import (
    calculate_urge_status,
    get_today,
)


log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PromotionService:
    """推广合作业务服务。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = PromotionRepository(session)
        self._style_repo = StyleRepository(session)
        self._sku_repo = SkuRepository(session)
        self._blogger_repo = BloggerRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
        self._audit = AuditService(session)

    # ============================================================
    # CRUD: create
    # ============================================================

    async def create_promotion(
        self, payload: PromotionCreate, user: User
    ) -> PromotionResponse:
        """EP05-S02 创建推广 + 自动 internal_code + 重复检测."""
        # 1. 引用完整性
        style = await self._style_repo.get_by_id(payload.style_id)
        if style is None:
            raise InvalidStyleReferenceError(
                f"款式 {payload.style_id} 不存在或已删除"
            )

        sku: Sku | None = None
        if payload.sku_id is not None:
            sku = await self._sku_repo.get_by_id(payload.sku_id)
            if sku is None:
                raise InvalidSkuReferenceError(
                    f"SKU {payload.sku_id} 不存在或已删除"
                )
            if sku.style_id != payload.style_id:
                raise InvalidSkuReferenceError(
                    f"SKU {payload.sku_id} 不属于款式 {payload.style_id}",
                    details={
                        "sku_style_id": str(sku.style_id),
                        "expected_style_id": str(payload.style_id),
                    },
                )

        blogger = await self._blogger_repo.get_by_id(payload.blogger_id)
        if blogger is None:
            raise InvalidBloggerReferenceError(
                f"博主 {payload.blogger_id} 不存在或已删除"
            )

        # 2. 字段写权限（quote_amount 可写）
        await self._check_amount_write_permission(payload, user)

        # 3. 取 tenant_code 用于 internal_code 前缀
        tenant_code = await self._get_tenant_code(user.tenant_id)

        # 4. 序列号原子获取（FB2）
        next_seq = await self._repo.next_internal_sequence(
            tenant_id=user.tenant_id,
            date_key=payload.cooperation_date,
        )
        internal_code = format_internal_code(
            tenant_code=tenant_code,
            cooperation_date=payload.cooperation_date,
            sequence=next_seq,
        )

        # 5. 快照字段计算
        quote_amount = (
            payload.quote_amount
            if payload.quote_amount is not None
            else (blogger.quote if blogger.quote is not None else None)
        )
        if quote_amount is None:
            # 业务规则 BR-U04-10：blogger.quote 为 NULL 时 PR 必须显式传值
            raise PublishUrlRequiredError(  # 复用 422 类异常通用消息
                "blogger.quote 为空时必须显式传 quote_amount",
                details={"field": "quote_amount"},
            )
        cost_snapshot = sku.cost_price if sku else None
        style_short_name = style.short_name or style.style_name

        # 6. 创建实体
        promotion = Promotion(
            style_id=payload.style_id,
            sku_id=payload.sku_id,
            blogger_id=payload.blogger_id,
            pr_id=user.id,
            internal_code=internal_code,
            style_code_snapshot=style.style_code,
            style_short_name_snapshot=style_short_name,
            quote_amount=quote_amount,
            cost_snapshot=cost_snapshot,
            platform=payload.platform,
            cooperation_date=payload.cooperation_date,
            scheduled_publish_date=payload.scheduled_publish_date,
            note_title=payload.note_title,
            remark=payload.remark,
            publish_status=PublishStatus.UNPUBLISHED.value,
            recall_status=RecallStatus.NOT_RECALLED.value,
            settlement_status=SettlementStatus.NOT_REVIEWED.value,
            is_active=True,
        )
        self._repo.add(promotion)
        await self._session.flush()

        # 7. 重复检测（EP05-S04 warning，非阻塞）
        duplicates = await self._repo.find_active_duplicate(
            style_id=payload.style_id,
            blogger_id=payload.blogger_id,
            exclude_id=promotion.id,
        )

        # 8. 审计：脱敏
        after_marker: dict[str, Any] = {
            "internal_code": internal_code,
            "publish_status": PublishStatus.UNPUBLISHED.value,
        }
        if quote_amount is not None:
            after_marker["quote_amount_changed"] = True
        if cost_snapshot is not None:
            after_marker["cost_snapshot_changed"] = True

        await self._audit.log(
            action="promotion.create",
            resource="promotion",
            resource_id=promotion.id,
            after=after_marker,
            user_id=user.id,
        )
        await self._session.commit()

        # 9. 返回（含重复警告）
        response = await self._to_response(promotion, user)
        if duplicates:
            response = response.model_copy(
                update={
                    "duplicate_warnings": [
                        PromotionDuplicateWarning(
                            promotion_id=d.id,
                            internal_code=d.internal_code,
                            publish_status=d.publish_status,
                            cooperation_date=d.cooperation_date,
                        )
                        for d in duplicates
                    ]
                }
            )
        return response

    # ============================================================
    # CRUD: update
    # ============================================================

    async def update_promotion(
        self,
        promotion_id: UUID,
        payload: PromotionUpdate,
        user: User,
    ) -> PromotionResponse:
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        # 字段写权限
        await self._check_amount_write_permission(payload, user)

        # SKU 改了重新校验
        if (
            "sku_id" in payload.model_fields_set
            and payload.sku_id is not None
            and payload.sku_id != promotion.sku_id
        ):
            sku = await self._sku_repo.get_by_id(payload.sku_id)
            if sku is None or sku.style_id != promotion.style_id:
                raise InvalidSkuReferenceError(
                    f"SKU {payload.sku_id} 不存在或不属于款式 {promotion.style_id}"
                )

        changes = compute_promotion_changes(promotion, payload)
        if not changes:
            return await self._to_response(promotion, user)

        # 应用变更
        for field in changes:
            new_value = getattr(payload, field)
            setattr(promotion, field, new_value)

        await self._session.flush()

        # 审计：仅敏感字段 + 敏感值脱敏
        audit_changes = build_promotion_audit_changes(changes)
        if audit_changes:
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            for k, v in audit_changes.items():
                if isinstance(v, dict):
                    before[k] = v["before"]
                    after[k] = v["after"]
                else:
                    after[k] = v
            await self._audit.log(
                action="promotion.update",
                resource="promotion",
                resource_id=promotion.id,
                before=before or None,
                after=after,
                user_id=user.id,
            )
        await self._session.commit()
        return await self._to_response(promotion, user)


    # ============================================================
    # Read
    # ============================================================

    async def get_promotion(
        self, promotion_id: UUID, user: User
    ) -> PromotionResponse:
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")
        return await self._to_response(promotion, user)

    async def list_promotions(
        self,
        *,
        filters: ApiPromotionListFilters,
        page: int,
        page_size: int,
        user: User,
    ) -> PromotionPage:
        """列表 + CTE（FB8 + Pattern P-U04-04）.

        match 降级：业务未匹配返回空数组；系统失败让异常自然冒泡（不 try/except）。
        """
        today = get_today()

        repo_filters = RepoPromotionListFilters(
            keyword=filters.keyword,
            publish_status=(
                filters.publish_status.value if filters.publish_status else None
            ),
            recall_status=(
                filters.recall_status.value if filters.recall_status else None
            ),
            settlement_status=(
                filters.settlement_status.value
                if filters.settlement_status
                else None
            ),
            platform=filters.platform,
            blogger_id=filters.blogger_id,
            style_id=filters.style_id,
            pr_id=filters.pr_id,
            cooperation_date_from=filters.cooperation_date_from,
            cooperation_date_to=filters.cooperation_date_to,
            scheduled_publish_date_from=filters.scheduled_publish_date_from,
            scheduled_publish_date_to=filters.scheduled_publish_date_to,
            is_active=filters.is_active,
            only_dual_platform=filters.only_dual_platform,
            is_hit=filters.is_hit,
            hit_threshold=HIT_THRESHOLD_LIKE_COUNT,
        )

        rows, total = await self._repo.list_with_cte(
            tenant_id=user.tenant_id,
            filters=repo_filters,
            page=page,
            page_size=page_size,
            today=today,
            urge_threshold_days=URGE_THRESHOLD_DAYS,
            important_threshold_days=IMPORTANT_THRESHOLD_DAYS,
        )

        promotion_search_results_count.observe(total)

        # 用 CTE 计算结果填充响应（避免重复计算 urge_status / dual_platform）
        items = [
            await self._to_response(
                row.promotion,
                user,
                today=today,
                urge_status_override=row.urge_status,
                dual_platform_override=row.dual_platform,
            )
            for row in rows
        ]
        return PromotionPage(
            items=items, total=total, page=page, page_size=page_size
        )

    # ============================================================
    # 状态推进（6 个）
    # ============================================================

    async def publish(
        self,
        promotion_id: UUID,
        payload: PromotionPublishRequest,
        user: User,
    ) -> PromotionResponse:
        """EP05-S07: 发布."""
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        # 业务前置校验（友好错误）
        PublishStatusMachine.assert_can_transition(
            from_state=promotion.publish_status,
            to_state=PublishStatus.PUBLISHED.value,
            action="publish",
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
        except Exception as exc:  # noqa: BLE001
            # 通知类事件失败不阻塞主流程；记一条降级 audit
            log.exception("promotion_published_event_dispatch_failed")
            await self._log_event_dispatch_failure(
                published_event, exc, user, blocking=False
            )

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
        await self._session.commit()
        return await self._to_response(updated, user)


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
                {"recall_reason": payload.recall_reason}
                if payload.recall_reason
                else None
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

    async def recall_success(
        self, promotion_id: UUID, user: User
    ) -> PromotionResponse:
        """EP05-S09: 召回成功（终态）."""
        return await self._recall_finish(
            promotion_id=promotion_id,
            user=user,
            to_state=RecallStatus.RECALLED_SUCCESS.value,
            action_log="promotion.recall_success",
        )

    async def recall_failure(
        self, promotion_id: UUID, user: User
    ) -> PromotionResponse:
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

    async def review(
        self,
        promotion_id: UUID,
        payload: PromotionReviewRequest,
        user: User,
    ) -> PromotionResponse:
        """EP05-S13: PR 主管审核（approve / reject）.

        approve 时同事务发 SettlementRequested 事件（FB1：required_handler）。
        失败时 audit 脱敏 + 兜底（FB5）。
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

        if payload.action == ReviewAction.APPROVE:
            to_state = SettlementStatus.PENDING_PAYMENT.value
            action_name = "approve"
        else:  # REJECT
            if not payload.review_reason:
                raise ReviewReasonRequiredError("驳回时 review_reason 必填")
            to_state = SettlementStatus.REJECTED.value
            action_name = "reject"

        SettlementStatusMachine.assert_can_transition(
            from_state=promotion.settlement_status,
            to_state=to_state,
            action=action_name,
        )

        now = _utcnow()
        updated = await self._repo.update_state(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
            from_state_field="settlement_status",
            from_state_value=SettlementStatus.PENDING_REVIEW.value,
            to_state_value=to_state,
            extra_fields={
                "reviewed_by": user.id,
                "reviewed_at": now,
                "review_action": payload.action.value,
                "review_reason": payload.review_reason,
            },
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
            },
            user_id=user.id,
        )

        # approve 时发强一致事件（FB1）
        if payload.action == ReviewAction.APPROVE:
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
                except Exception:  # noqa: BLE001
                    pass
                await self._log_event_dispatch_failure(
                    event, exc, user, blocking=True
                )
                raise

        await self._session.commit()
        return await self._to_response(updated, user)

    # ============================================================
    # 软停用 / 内部 API
    # ============================================================

    async def soft_delete_promotion(
        self, promotion_id: UUID, user: User
    ) -> None:
        """通用软停用：is_active=false（与状态机正交）."""
        promotion = await self._repo.get_by_id(promotion_id)
        if promotion is None:
            raise PromotionNotFoundError(f"推广 {promotion_id} 不存在")

        deactivated = await self._repo.soft_deactivate(
            promotion_id=promotion_id,
            tenant_id=user.tenant_id,
        )
        if deactivated is None:
            raise StateTransitionConflictError(
                "推广已被停用或软删，请刷新后重试",
            )

        await self._audit.log(
            action="promotion.delete",
            resource="promotion",
            resource_id=promotion_id,
            user_id=user.id,
        )
        await self._session.commit()

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


    # ============================================================
    # Private helpers
    # ============================================================

    async def _check_amount_write_permission(
        self,
        payload: PromotionCreate | PromotionUpdate,
        user: User,
    ) -> None:
        """字段写权限校验（quote_amount）— U09 经 core 注册表 + 字段级 override。"""
        fields_set = payload.model_fields_set
        if "quote_amount" not in fields_set:
            return
        value = getattr(payload, "quote_amount", None)
        if value is None:
            # PATCH 显式传 None 等同于不修改业务上不要求权限
            return
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        if not can_write_field("promotion", "quote_amount", ctx):
            raise FieldPermissionDenied(field="quote_amount", entity="promotion")

    async def _to_response(
        self,
        promotion: Promotion,
        user: User,
        *,
        today: Any = None,
        urge_status_override: str | None = None,
        dual_platform_override: bool | None = None,
    ) -> PromotionResponse:
        """组装响应：字段权限过滤 + 衍生字段计算.

        Args:
            urge_status_override: 列表查询时由 SQL CTE 计算后透传，避免重复计算。
            dual_platform_override: 同上。
            today: 列表查询时由 service 层 get_today() 透传，单条响应时缺省现算。
        """
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        can_see_quote = can_read_field("promotion", "quote_amount", ctx)
        can_see_cost = can_read_field("promotion", "cost_snapshot", ctx)

        # 衍生字段计算
        if today is None:
            today = get_today()
        urge_status = (
            urge_status_override
            if urge_status_override is not None
            else calculate_urge_status(
                publish_status=promotion.publish_status,
                scheduled_publish_date=promotion.scheduled_publish_date,
                today=today,
                urge_threshold_days=URGE_THRESHOLD_DAYS,
                important_threshold_days=IMPORTANT_THRESHOLD_DAYS,
            )
        )
        if dual_platform_override is None:
            dual_platform = await self._repo.has_other_platforms_for_style(
                style_id=promotion.style_id,
                platform=promotion.platform,
                exclude_id=promotion.id,
            )
        else:
            dual_platform = dual_platform_override

        effective_like = calculate_effective_like_count(
            platform=promotion.platform, like_count=promotion.like_count
        )
        is_hit = calculate_is_hit(
            like_count=promotion.like_count, threshold=HIT_THRESHOLD_LIKE_COUNT
        )
        cpl = calculate_cpl(
            quote_amount=promotion.quote_amount,
            effective_like_count=effective_like,
        )

        return PromotionResponse(
            id=promotion.id,
            internal_code=promotion.internal_code,
            style_id=promotion.style_id,
            sku_id=promotion.sku_id,
            blogger_id=promotion.blogger_id,
            pr_id=promotion.pr_id,
            style_code_snapshot=promotion.style_code_snapshot,
            style_short_name_snapshot=promotion.style_short_name_snapshot,
            quote_amount=(
                promotion.quote_amount if can_see_quote else None
            ),
            cost_snapshot=(
                promotion.cost_snapshot if can_see_cost else None
            ),
            platform=promotion.platform,
            cooperation_date=promotion.cooperation_date,
            scheduled_publish_date=promotion.scheduled_publish_date,
            actual_publish_date=promotion.actual_publish_date,
            publish_url=promotion.publish_url,
            cancel_reason=promotion.cancel_reason,
            recall_reason=promotion.recall_reason,
            like_count=promotion.like_count,
            note_title=promotion.note_title,
            remark=promotion.remark,
            publish_status=promotion.publish_status,
            recall_status=promotion.recall_status,
            settlement_status=promotion.settlement_status,
            reviewed_by=promotion.reviewed_by,
            reviewed_at=promotion.reviewed_at,
            review_action=promotion.review_action,
            review_reason=promotion.review_reason,
            is_active=promotion.is_active,
            created_at=promotion.created_at,
            updated_at=promotion.updated_at,
            urge_status=urge_status,
            dual_platform=dual_platform,
            effective_like_count=effective_like,
            is_hit=is_hit,
            cpl=cpl if can_see_quote else None,
            duplicate_warnings=[],
        )

    async def _log_event_dispatch_failure(
        self,
        event: Any,
        exc: Exception,
        user: User,
        *,
        blocking: bool,
    ) -> None:
        """事件分发失败的独立 audit（FB5 脱敏 + 兜底）.

        严格脱敏 — 不写 ``str(exc)`` / SQL / 金额 / 内部路径。
        audit 自身写入失败不能覆盖原异常。

        Args:
            blocking: True = 强一致事件（SettlementRequested），调用方会重新 raise；
                      False = 通知类事件（PromotionPublished），不阻塞主流程。
        """
        safe_payload = {
            "event_type": getattr(event, "event_type", "unknown"),
            "event_id": str(getattr(event, "event_id", "")),
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", None),
            "promotion_id": str(getattr(event, "promotion_id", "") or ""),
            "request_id": request_id_ctx.get() or None,
            "blocking": blocking,
        }

        # 用独立 bypass session 写 audit，避免被原事务回滚带走
        token = bypass_rls_ctx.set(True)
        try:
            try:
                async with AsyncSessionBypass() as audit_session:
                    audit_service = AuditService(audit_session)
                    await audit_service.log(
                        action="promotion.event_dispatch_failed",
                        resource="promotion",
                        resource_id=getattr(event, "promotion_id", None),
                        after=safe_payload,
                        actor_type="system",
                        user_id=user.id,
                    )
                    await audit_session.commit()
            except Exception as audit_exc:  # noqa: BLE001
                # 兜底：audit 写失败仅 log，不覆盖原异常
                log.exception(
                    "audit_for_event_failure_itself_failed",
                    extra={
                        "original_error": type(exc).__name__,
                        "audit_error": type(audit_exc).__name__,
                    },
                )
                # 不重新抛 audit_exc，让原 exc 继续上抛
        finally:
            bypass_rls_ctx.reset(token)

    async def _get_tenant_code(self, tenant_id: UUID) -> str:
        """取 tenant.code 用于 internal_code 前缀."""
        result = await self._session.execute(
            select(Tenant.code).where(Tenant.id == tenant_id)
        )
        code = result.scalar_one_or_none()
        return str(code or "")


__all__ = ["PromotionService"]
