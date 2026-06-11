"""U05 finance 服务层（SettlementService）。

按 nfr-design/logical-components.md §4.1 + nfr-design-patterns.md P-U05-01~04：

业务编排层：
- 状态推进（4 个）：review（approve/reject）/ fill_payment_amount / upload_payment_proof / resubmit
- add_extra_item（运费 / 赞奖）
- 读查询：get_settlement / list_settlements / daily_summary 双口径
- 内部：on_settlement_requested 由 listeners.py 调用（不在 service 内）

关键设计：
- 状态机推进通过 ``repository.update_state``（FB7：UPDATE WHERE old_state RETURNING）
- mark_paid 同事务发 SettlementPaid 反向事件（FB5 通知类 — 失败不阻塞主流程）
- **失败处理不对称**：mark_paid dispatch 失败不重新 raise（与 U04 review approve raise 不同）
- attachment 6 项强校验（FB4：ProofAttachmentValidator）
- 字段写权限硬编码（待 U09 清理）
- 财务记录不可替换（FB3：无 soft_delete 接口）
- 双口径汇总用 get_today 入口（FB8）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events as event_bus
from app.core.attachment import attachment_service as _default_attachment_service
from app.core.audit import AuditService
from app.core.db import AsyncSessionBypass
from app.core.metrics import settlement_state_transitions_total
from app.core.security.field_permissions import (
    build_field_perm_context,
    can_read_field,
    can_write_field,
)
from app.core.security.permissions import EffectivePermissions
from app.core.tenancy import bypass_rls_ctx, request_id_ctx
from app.modules.auth.domain import merge_permissions
from app.modules.auth.models import User
from app.modules.auth.repository import PermissionRepository, RoleRepository

from app.modules.finance.attachment_validator import ProofAttachmentValidator
from app.modules.finance.enums import SettlementStatus
from app.modules.finance.events import SettlementPaid
from app.modules.finance.exceptions import (
    ExtraItemNotAllowedError,
    FieldPermissionDenied,
    PaymentFieldMissingError,
    ReviewReasonRequiredError,
    SelfReviewForbiddenError,
    SettlementNotFoundError,
    StateTransitionConflictError,
)
from app.modules.finance.models import Settlement, SettlementExtraItem
from app.modules.finance.repository import (
    SettlementListFilters as RepoSettlementListFilters,
)
from app.modules.finance.repository import SettlementRepository
from app.modules.finance.schemas import (
    DailySummaryActivityBuckets,
    DailySummaryActivityResponse,
    DailySummaryAsOfBuckets,
    DailySummaryAsOfResponse,
    AmountBucket,
    SettlementExtraItemCreateRequest,
    SettlementExtraItemResponse,
    SettlementListFilters as ApiSettlementListFilters,
    SettlementPage,
    SettlementPaymentAmountRequest,
    SettlementPaymentProofRequest,
    SettlementResponse,
    SettlementReviewRequest,
)
from app.modules.finance.state_machines import SettlementStatusMachine
from app.modules.promotion.enums import ReviewAction
from app.modules.promotion.urge_calculator import get_today


log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SettlementService:
    """财务结款业务服务。"""

    def __init__(
        self,
        session: AsyncSession,
        attachment_service: Any = None,
    ) -> None:
        self._session = session
        self._repo = SettlementRepository(session)
        self._roles = RoleRepository(session)
        self._perms = PermissionRepository(session)
        self._audit = AuditService(session)
        self._attachment_service = attachment_service or _default_attachment_service
        self._validator = ProofAttachmentValidator(self._attachment_service)

    # ============================================================
    # 状态推进：review（approve / reject）
    # ============================================================

    async def review(
        self,
        settlement_id: UUID,
        payload: SettlementReviewRequest,
        user: User,
    ) -> SettlementResponse:
        """EP06-S03 / S04：PR 主管核查 / 驳回."""
        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")

        # 自审禁止（reviewer != settlement.pr_id，来自 promotion.pr_id 冗余）
        if settlement.pr_id is not None and settlement.pr_id == user.id:
            raise SelfReviewForbiddenError(
                "不允许审核自己提交的推广对应的结算单",
                details={
                    "settlement_id": str(settlement_id),
                    "user_id": str(user.id),
                },
            )

        from_status = settlement.settlement_status

        if payload.action == ReviewAction.APPROVE:
            to_status = SettlementStatus.PENDING_PAYMENT.value
            action_name = "approve"
            audit_action = "settlement.review.approve"
        else:  # REJECT
            if not payload.review_reason:
                raise ReviewReasonRequiredError("驳回时 review_reason 必填")
            to_status = SettlementStatus.REJECTED.value
            action_name = "reject"
            audit_action = "settlement.review.reject"

        SettlementStatusMachine.assert_can_transition(
            from_state=from_status,
            to_state=to_status,
            action=action_name,
        )

        now = _utcnow()
        updated = await self._repo.update_state(
            settlement_id=settlement_id,
            tenant_id=user.tenant_id,
            from_state_value=from_status,
            to_state_value=to_status,
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
                details={"settlement_id": str(settlement_id)},
            )

        settlement_state_transitions_total.labels(
            from_state=from_status, to_state=to_status
        ).inc()

        await self._audit.log(
            action=audit_action,
            resource="settlement",
            resource_id=settlement_id,
            after={
                "settlement_status": to_status,
                "review_action": payload.action.value,
            },
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user)

    # ============================================================
    # 状态推进：fill_payment_amount
    # ============================================================

    async def fill_payment_amount(
        self,
        settlement_id: UUID,
        payload: SettlementPaymentAmountRequest,
        user: User,
    ) -> SettlementResponse:
        """EP06-S06：PR 主管填写付款金额 → 待财务付款."""
        await self._check_payment_write_permission(user)

        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")

        SettlementStatusMachine.assert_can_transition(
            from_state=settlement.settlement_status,
            to_state=SettlementStatus.PENDING_FINANCE.value,
            action="fill_payment",
        )

        updated = await self._repo.update_state(
            settlement_id=settlement_id,
            tenant_id=user.tenant_id,
            from_state_value=SettlementStatus.PENDING_PAYMENT.value,
            to_state_value=SettlementStatus.PENDING_FINANCE.value,
            extra_fields={"payment_amount": payload.payment_amount},
        )
        if updated is None:
            raise StateTransitionConflictError(
                "结款状态已变更，请刷新后重试",
                details={"settlement_id": str(settlement_id)},
            )

        settlement_state_transitions_total.labels(
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            to_state=SettlementStatus.PENDING_FINANCE.value,
        ).inc()

        await self._audit.log(
            action="settlement.fill_payment",
            resource="settlement",
            resource_id=settlement_id,
            after={
                "payment_amount_changed": True,
                "settlement_status": SettlementStatus.PENDING_FINANCE.value,
            },
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user)


    # ============================================================
    # 状态推进：upload_payment_proof（mark_paid + FB4 + FB5）
    # ============================================================

    async def upload_payment_proof(
        self,
        settlement_id: UUID,
        payload: SettlementPaymentProofRequest,
        user: User,
    ) -> SettlementResponse:
        """EP06-S07：财务上传付款截图 → 已付款.

        FB4：attachment 6 项强校验（ProofAttachmentValidator）。
        FB5：mark_paid 同事务发 SettlementPaid 反向事件（通知类 — 失败不阻塞主流程）。
        """
        await self._check_proof_upload_permission(user)

        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")

        # payment_date ≤ today（Asia/Shanghai，FB8 复用 get_today）
        if payload.payment_date > get_today():
            raise PaymentFieldMissingError(
                "payment_date 不能晚于今天",
                details={"payment_date": payload.payment_date.isoformat()},
            )

        # FB4：attachment 6 项强校验
        await self._validator.validate(
            session=self._session,
            attachment_id=payload.payment_proof_attachment_id,
            tenant_id=user.tenant_id,
        )

        SettlementStatusMachine.assert_can_transition(
            from_state=settlement.settlement_status,
            to_state=SettlementStatus.PAID.value,
            action="mark_paid",
        )

        updated = await self._repo.update_state(
            settlement_id=settlement_id,
            tenant_id=user.tenant_id,
            from_state_value=SettlementStatus.PENDING_FINANCE.value,
            to_state_value=SettlementStatus.PAID.value,
            extra_fields={
                "payment_date": payload.payment_date,
                "payment_proof_attachment_id": payload.payment_proof_attachment_id,
                "paid_by": user.id,
            },
        )
        if updated is None:
            raise StateTransitionConflictError(
                "结款状态已变更或已付款不可重复，请刷新后重试",
                details={"settlement_id": str(settlement_id)},
            )

        settlement_state_transitions_total.labels(
            from_state=SettlementStatus.PENDING_FINANCE.value,
            to_state=SettlementStatus.PAID.value,
        ).inc()

        await self._audit.log(
            action="settlement.mark_paid",
            resource="settlement",
            resource_id=settlement_id,
            after={
                "payment_date": payload.payment_date.isoformat(),
                "payment_amount_changed": True,
                "attachment_id_changed": True,
                "settlement_status": SettlementStatus.PAID.value,
            },
            user_id=user.id,
        )

        # FB5：发 SettlementPaid 反向事件（通知类 — 失败不阻塞主流程，与 U04 不对称）
        event = SettlementPaid(
            event_id=uuid4(),
            timestamp=_utcnow(),
            tenant_id=updated.tenant_id,
            settlement_id=updated.id,
            promotion_id=updated.promotion_id,
            payment_amount=updated.payment_amount or Decimal("0"),
            payment_date=payload.payment_date,
            paid_by=user.id,
        )
        try:
            await event_bus.dispatch(event, session=self._session)
        except Exception as exc:  # noqa: BLE001
            # 通知类事件失败不阻塞主流程；与 U04 review approve raise 不对称（FB5）
            log.exception("settlement_paid_dispatch_failed")
            try:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            except Exception:  # noqa: BLE001
                pass
            await self._log_event_dispatch_failure(event, exc, user, blocking=False)
            # 不重新 raise — 让 commit 继续（mark_paid 主流程已成功）

        await self._session.commit()
        return await self._to_response(updated, user)

    # ============================================================
    # 状态推进：resubmit（已驳回 → 待核查）
    # ============================================================

    async def resubmit(
        self, settlement_id: UUID, user: User
    ) -> SettlementResponse:
        """已驳回 → 待核查（PR 修改后重新提交）。"""
        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")

        SettlementStatusMachine.assert_can_transition(
            from_state=settlement.settlement_status,
            to_state=SettlementStatus.PENDING_REVIEW.value,
            action="resubmit",
        )

        updated = await self._repo.update_state(
            settlement_id=settlement_id,
            tenant_id=user.tenant_id,
            from_state_value=SettlementStatus.REJECTED.value,
            to_state_value=SettlementStatus.PENDING_REVIEW.value,
        )
        if updated is None:
            raise StateTransitionConflictError(
                "结款状态已变更，请刷新后重试",
                details={"settlement_id": str(settlement_id)},
            )

        settlement_state_transitions_total.labels(
            from_state=SettlementStatus.REJECTED.value,
            to_state=SettlementStatus.PENDING_REVIEW.value,
        ).inc()

        await self._audit.log(
            action="settlement.resubmit",
            resource="settlement",
            resource_id=settlement_id,
            after={"settlement_status": SettlementStatus.PENDING_REVIEW.value},
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user)


    # ============================================================
    # Extra Item
    # ============================================================

    async def add_extra_item(
        self,
        settlement_id: UUID,
        payload: SettlementExtraItemCreateRequest,
        user: User,
    ) -> SettlementResponse:
        """EP06-S05：PR 主管增加结算项（运费 / 赞奖）+ total_amount 重算。"""
        # 字段写权限（admin / pr_manager）— U09 经 core 注册表 + 字段级 override
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        if not can_write_field("settlement", "payment_amount", ctx):
            raise FieldPermissionDenied(field="extra_item", entity="settlement")

        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")

        # 仅"待付款"状态允许（BR-U05-40）
        if settlement.settlement_status != SettlementStatus.PENDING_PAYMENT.value:
            raise ExtraItemNotAllowedError(
                "仅'待付款'状态可增加结算项",
                details={"settlement_status": settlement.settlement_status},
            )

        item = SettlementExtraItem(
            id=uuid4(),
            tenant_id=user.tenant_id,
            settlement_id=settlement_id,
            item_type=payload.item_type.value,
            amount=payload.amount,
            remark=payload.remark,
            created_by=user.id,
        )
        self._repo.add_extra_item(item)
        await self._session.flush()

        # 重算 total_amount = amount + SUM(extra_items)
        extra_sum = await self._repo.sum_extra_items(settlement_id=settlement_id)
        new_total = settlement.amount + extra_sum
        updated = await self._repo.update_total_amount(
            settlement_id=settlement_id,
            tenant_id=user.tenant_id,
            total_amount=new_total,
        )
        if updated is None:
            raise StateTransitionConflictError(
                "结款状态已变更，请刷新后重试",
                details={"settlement_id": str(settlement_id)},
            )

        await self._audit.log(
            action="settlement.add_extra_item",
            resource="settlement",
            resource_id=settlement_id,
            after={
                "item_type": payload.item_type.value,
                "total_amount_changed": True,
            },
            user_id=user.id,
        )
        await self._session.commit()
        return await self._to_response(updated, user)

    # ============================================================
    # Read
    # ============================================================

    async def get_settlement(
        self, settlement_id: UUID, user: User
    ) -> SettlementResponse:
        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")
        return await self._to_response(settlement, user)

    async def list_settlements(
        self,
        *,
        filters: ApiSettlementListFilters,
        page: int,
        page_size: int,
        user: User,
    ) -> SettlementPage:
        """列表 + 分页。

        PR 角色（非 PAYMENT_VISIBLE_ROLES）自动限自己提交的（is_my_only）。
        系统失败让异常自然冒泡（不 try/except DB 异常 → 5xx + Sentry）。
        """
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        can_see_payment = can_read_field("settlement", "amount", ctx)

        # PR 角色（不可见金额）→ 限自己提交的
        is_my_only = filters.is_my or not can_see_payment

        repo_filters = RepoSettlementListFilters(
            keyword=filters.keyword,
            settlement_status=(
                filters.settlement_status.value
                if filters.settlement_status
                else None
            ),
            promotion_id=filters.promotion_id,
            blogger_id=filters.blogger_id,
            style_id=filters.style_id,
            pr_id=filters.pr_id,
            reviewed_by=filters.reviewed_by,
            paid_by=filters.paid_by,
            created_at_from=filters.created_at_from,
            created_at_to=filters.created_at_to,
            payment_date_from=filters.payment_date_from,
            payment_date_to=filters.payment_date_to,
            amount_from=filters.amount_from,
            amount_to=filters.amount_to,
            payment_amount_from=filters.payment_amount_from,
            payment_amount_to=filters.payment_amount_to,
            is_my_only=is_my_only,
        )

        items, total = await self._repo.list_with_filters(
            tenant_id=user.tenant_id,
            filters=repo_filters,
            page=page,
            page_size=page_size,
            current_user_id=user.id,
        )

        return SettlementPage(
            items=[
                await self._to_response(s, user, include_extra_items=False)
                for s in items
            ],
            total=total,
            page=page,
            page_size=page_size,
        )

    # ============================================================
    # 双口径汇总（FB7 + FB8）
    # ============================================================

    async def get_daily_summary_as_of(
        self, *, date_value: Any = None, user: User
    ) -> DailySummaryAsOfResponse:
        """口径 B：截至当日各状态快照（FB7）。"""
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        if not can_read_field("settlement", "amount", ctx):
            raise FieldPermissionDenied(field="settlement_amount", entity="settlement")

        target_date = date_value or get_today()  # FB8 时区入口
        raw = await self._repo.daily_summary_as_of(
            tenant_id=user.tenant_id, date_value=target_date
        )

        def _bucket(status_label: str) -> AmountBucket:
            data = raw.get(status_label, {"count": 0, "total_amount": "0"})
            return AmountBucket(
                count=data["count"], total_amount=Decimal(data["total_amount"])
            )

        outstanding_count = sum(
            raw.get(s, {"count": 0})["count"]
            for s in ("待核查", "待付款", "待财务付款")
        )
        outstanding_amount = sum(
            (Decimal(raw.get(s, {"total_amount": "0"})["total_amount"])
             for s in ("待核查", "待付款", "待财务付款")),
            Decimal("0"),
        )

        return DailySummaryAsOfResponse(
            kind="as_of",
            date=target_date,
            as_of=DailySummaryAsOfBuckets(
                pending_review=_bucket("待核查"),
                pending_payment=_bucket("待付款"),
                pending_finance=_bucket("待财务付款"),
                paid=_bucket("已付款"),
                rejected=_bucket("已驳回"),
            ),
            outstanding_total=AmountBucket(
                count=outstanding_count,
                total_amount=outstanding_amount,
            ),
        )

    async def get_daily_summary_activity(
        self, *, date_value: Any = None, user: User
    ) -> DailySummaryActivityResponse:
        """口径 A：当天发生的动作（FB7）。"""
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        if not can_read_field("settlement", "amount", ctx):
            raise FieldPermissionDenied(field="settlement_amount", entity="settlement")

        target_date = date_value or get_today()  # FB8 时区入口
        raw = await self._repo.daily_summary_activity(
            tenant_id=user.tenant_id, date_value=target_date
        )

        def _bucket(key: str) -> AmountBucket:
            data = raw[key]
            return AmountBucket(
                count=data["count"], total_amount=Decimal(data["total_amount"])
            )

        return DailySummaryActivityResponse(
            kind="activity",
            date=target_date,
            activity=DailySummaryActivityBuckets(
                newly_created=_bucket("newly_created"),
                newly_approved=_bucket("newly_approved"),
                newly_paid=_bucket("newly_paid"),
                newly_rejected=_bucket("newly_rejected"),
            ),
        )


    # ============================================================
    # Private helpers
    # ============================================================

    async def _check_payment_write_permission(self, user: User) -> None:
        """fill_payment 写权限（U09: settlement.payment_amount 写）。"""
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        if not can_write_field("settlement", "payment_amount", ctx):
            raise FieldPermissionDenied(field="payment_amount", entity="settlement")

    async def _check_proof_upload_permission(self, user: User) -> None:
        """upload_payment_proof 权限（U09: finance.settlement:pay scope — admin / finance）。"""
        role_scopes, grants, revokes = await self._perms.list_scopes_for_user(user.id)
        perms = EffectivePermissions(
            user_id=str(user.id),
            scopes=merge_permissions(role_scopes, grants, revokes),
        )
        if not perms.has("finance.settlement", "pay"):
            raise FieldPermissionDenied(
                field="payment_proof_attachment_id", entity="settlement"
            )

    async def _to_response(
        self,
        settlement: Settlement,
        user: User,
        *,
        include_extra_items: bool = True,
    ) -> SettlementResponse:
        """组装响应：字段权限过滤 + attachment 签名 URL.

        非 PAYMENT_VISIBLE_ROLES 角色 → amount / total_amount / payment_amount /
        payment_proof_signed_url 全部置 None。
        """
        ctx = await build_field_perm_context(user.id, self._roles, self._perms)
        can_see_amount = can_read_field("settlement", "amount", ctx)
        can_see_total = can_read_field("settlement", "total_amount", ctx)
        can_see_payment_amount = can_read_field("settlement", "payment_amount", ctx)
        # 签名 URL 可见性沿用金额可见口径（amount）
        can_see_payment = can_see_amount

        # 签名 URL（仅可见金额角色 + 已有 attachment 时生成）
        signed_url: str | None = None
        if (
            can_see_payment
            and settlement.payment_proof_attachment_id is not None
        ):
            attachment = await self._attachment_service.get_by_id(
                session=self._session,
                attachment_id=settlement.payment_proof_attachment_id,
            )
            if attachment is not None and attachment.status == "ready":
                try:
                    signed_url = self._attachment_service.get_signed_url(
                        "private", attachment.r2_key, expires_in=900
                    )
                except Exception:  # noqa: BLE001
                    log.warning("signed_url_generation_failed")

        extra_items: list[SettlementExtraItemResponse] = []
        if include_extra_items:
            raw_items = await self._repo.list_extra_items(
                settlement_id=settlement.id
            )
            extra_items = [
                SettlementExtraItemResponse.model_validate(it)
                for it in raw_items
            ]

        return SettlementResponse(
            id=settlement.id,
            settlement_no=settlement.settlement_no,
            promotion_id=settlement.promotion_id,
            blogger_id=settlement.blogger_id,
            style_id=settlement.style_id,
            pr_id=settlement.pr_id,
            amount=settlement.amount if can_see_amount else None,
            total_amount=settlement.total_amount if can_see_total else None,
            payment_amount=(
                settlement.payment_amount if can_see_payment_amount else None
            ),
            payment_date=settlement.payment_date,
            payment_proof_attachment_id=settlement.payment_proof_attachment_id,
            payment_proof_signed_url=signed_url,
            note_title=settlement.note_title,
            remark=settlement.remark,
            settlement_status=settlement.settlement_status,
            reviewed_by=settlement.reviewed_by,
            reviewed_at=settlement.reviewed_at,
            review_action=settlement.review_action,
            review_reason=settlement.review_reason,
            paid_by=settlement.paid_by,
            created_at=settlement.created_at,
            updated_at=settlement.updated_at,
            extra_items=extra_items,
        )

    async def _log_event_dispatch_failure(
        self,
        event: Any,
        exc: Exception,
        user: User,
        *,
        blocking: bool,
    ) -> None:
        """事件分发失败的独立 audit（FB5 脱敏 + 兜底，复用 U04 模式）.

        严格脱敏 — 不写 ``str(exc)`` / SQL / 金额 / 内部路径。
        audit 自身写入失败不能覆盖原异常。

        Args:
            blocking: True = 强一致事件（U05 无此场景，仅 mark_paid 通知类 blocking=False）。
        """
        safe_payload = {
            "event_type": getattr(event, "event_type", "unknown"),
            "event_id": str(getattr(event, "event_id", "")),
            "error_type": type(exc).__name__,
            "error_code": getattr(exc, "code", None),
            "settlement_id": str(getattr(event, "settlement_id", "") or ""),
            "promotion_id": str(getattr(event, "promotion_id", "") or ""),
            "request_id": request_id_ctx.get() or None,
            "blocking": blocking,
        }

        token = bypass_rls_ctx.set(True)
        try:
            try:
                async with AsyncSessionBypass() as audit_session:
                    audit_service = AuditService(audit_session)
                    await audit_service.log(
                        action="settlement.paid_sync_failed",
                        resource="settlement",
                        resource_id=getattr(event, "settlement_id", None),
                        after=safe_payload,
                        actor_type="system",
                        user_id=user.id,
                    )
                    await audit_session.commit()
            except Exception as audit_exc:  # noqa: BLE001
                log.exception(
                    "audit_for_event_failure_itself_failed",
                    extra={
                        "original_error": type(exc).__name__,
                        "audit_error": type(audit_exc).__name__,
                    },
                )
        finally:
            bypass_rls_ctx.reset(token)


__all__ = ["SettlementService"]
