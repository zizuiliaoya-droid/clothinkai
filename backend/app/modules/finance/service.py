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
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import events as event_bus
from app.core.attachment import attachment_service as _default_attachment_service
from app.core.attachment import check_image_payload
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
from app.modules.finance.attachment_validator import (
    MAX_SIZE_BYTES as PROOF_MAX_SIZE_BYTES,
)
from app.modules.finance.attachment_validator import ProofAttachmentValidator
from app.modules.finance.enums import SettlementStatus
from app.modules.finance.events import SettlementPaid, SettlementRejected
from app.modules.finance.exceptions import (
    ExtraItemNotAllowedError,
    FieldPermissionDenied,
    InvalidPaymentProofImageError,
    ReviewReasonRequiredError,
    SelfReviewForbiddenError,
    SettlementNotFoundError,
    SettlementTotalChangedError,
    StateTransitionConflictError,
)
from app.modules.finance.flow_doc import build_settlement_doc
from app.modules.finance.models import Settlement, SettlementExtraItem
from app.modules.finance.repository import (
    SettlementListFilters as RepoSettlementListFilters,
)
from app.modules.finance.repository import SettlementPromotionInfo, SettlementRepository
from app.modules.finance.schemas import (
    AmountBucket,
    DailySummaryActivityBuckets,
    DailySummaryActivityResponse,
    DailySummaryAsOfBuckets,
    DailySummaryAsOfResponse,
    SettlementExtraItemCreateRequest,
    SettlementExtraItemResponse,
    SettlementPage,
    SettlementPaymentAmountRequest,
    SettlementPaymentProofRequest,
    SettlementResponse,
    SettlementReviewRequest,
)
from app.modules.finance.schemas import (
    SettlementListFilters as ApiSettlementListFilters,
)
from app.modules.finance.state_machines import SettlementStatusMachine
from app.modules.flow.actor import load_flow_actor
from app.modules.flow.exceptions import FlowGateMissingError
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.flow.matrix import FlowActor, View, ui_for
from app.modules.importer.access import can_write as can_import_write
from app.modules.promotion.enums import ReviewAction
from app.modules.promotion.urge_calculator import get_today

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
        *,
        autocommit: bool = True,
    ) -> SettlementResponse:
        """核查（F0，只给上线前停在待核查的旧单；新单 → 422）/ 驳回（F5，待付款 / 待财务付款）.

        驳回在结款单条件 UPDATE 之后、commit 之前同一 session 发 ``SettlementRejected``（R6，D12）：
        推广单监听 0 行 / 抛错时异常冒泡，两张表一起回滚。``autocommit=False`` 时不提交，由调用方统一提交。
        """
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

        settlement_state_transitions_total.labels(from_state=from_status, to_state=to_status).inc()

        if payload.action == ReviewAction.APPROVE:
            # F0 旧单核查通过（只可能从待核查来）；驳回的 settlement.rejected 由推广侧监听写（R6）
            await self._append_line_event(
                updated,
                LineEventType.SETTLEMENT_LEGACY_APPROVED,
                actor_id=user.id,
                from_state=from_status,
                to_state=to_status,
            )
        else:
            # F5 → R6：同事务、异常冒泡（必须有监听，不按通知类容忍）
            await event_bus.dispatch(
                SettlementRejected(
                    event_id=uuid4(),
                    timestamp=now,
                    tenant_id=updated.tenant_id,
                    settlement_id=updated.id,
                    promotion_id=updated.promotion_id,
                    reason=payload.review_reason or "",
                    actor_id=user.id,
                    from_state=from_status,
                ),
                session=self._session,
            )

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
        if autocommit:
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
        """F3：填实付金额 → 待财务付款.

        顺序：字段写权限 → 状态机 → 实付 ≠ 应付（``total_amount``）时调整说明必填
        （422 ``FLOW_GATE_MISSING``，key = ``payment_adjust_note``）→ 条件 UPDATE（锁状态 + 读到的应付；
        状态变了 409 ``SETTLEMENT_STATE_CONFLICT``，仍待付款但应付变了 409 ``SETTLEMENT_TOTAL_CHANGED``）→ 事件。
        「不一致必须写说明」只对新录入强制：存量已付款单没有说明，库里不加 CHECK（计划 D2）。
        """
        await self._check_payment_write_permission(user)

        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")

        SettlementStatusMachine.assert_can_transition(
            from_state=settlement.settlement_status,
            to_state=SettlementStatus.PENDING_FINANCE.value,
            action="fill_payment",
        )

        note = payload.payment_adjust_note or None
        read_total = settlement.total_amount
        if payload.payment_amount != read_total and note is None:
            raise FlowGateMissingError([("payment_adjust_note", "调整说明")])

        # 说明必填是按读到的应付判的：条件 UPDATE 连应付一起锁，期间「增加结算项」改了应付 → 0 行
        updated = await self._repo.update_state(
            settlement_id=settlement_id,
            tenant_id=user.tenant_id,
            from_state_value=SettlementStatus.PENDING_PAYMENT.value,
            to_state_value=SettlementStatus.PENDING_FINANCE.value,
            extra_fields={"payment_amount": payload.payment_amount, "payment_adjust_note": note},
            expected_total_amount=read_total,
        )
        if updated is None:
            # 重读一次，分清是状态变了还是应付变了
            await self._session.refresh(settlement)
            if settlement.settlement_status == SettlementStatus.PENDING_PAYMENT.value:
                raise SettlementTotalChangedError(
                    "应付金额刚有变动，请刷新后重新填写实付",
                    details={"settlement_id": str(settlement_id)},
                )
            raise StateTransitionConflictError(
                "结款状态已变更，请刷新后重试",
                details={"settlement_id": str(settlement_id)},
            )

        settlement_state_transitions_total.labels(
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            to_state=SettlementStatus.PENDING_FINANCE.value,
        ).inc()

        # 金额存字符串（6.3），取落库后的值（两位小数）
        filled_payload: dict[str, Any] = {"payment_amount": str(updated.payment_amount)}
        if note is not None:
            filled_payload["payment_adjust_note"] = note
        await self._append_line_event(
            updated,
            LineEventType.SETTLEMENT_PAYMENT_FILLED,
            actor_id=user.id,
            from_state=SettlementStatus.PENDING_PAYMENT.value,
            to_state=SettlementStatus.PENDING_FINANCE.value,
            payload=filled_payload,
        )

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
        """EP06-S07：财务上传付款截图 → 已付款（引用一个已上传好的 attachment）.

        前端现在走 ``upload_payment_proof_file``（后端代传），这个入口保留给接口兼容。
        """
        updated = await self._mark_paid(settlement_id, payload, user)
        await self._session.commit()
        return await self._to_response(updated, user)

    async def upload_payment_proof_file(
        self,
        settlement_id: UUID,
        *,
        filename: str | None,
        mime_type: str | None,
        data: bytes,
        user: User,
    ) -> SettlementResponse:
        """财务上传付款截图（后端代传到私有桶）→ 已付款，一个请求完成。

        原来是「upload-init → 浏览器直传 R2 → complete → PUT payment-proof」四步。浏览器直传
        要求 bucket 配 CORS，生产私有桶没配：预检被 R2 拒成 403，前端只报一句
        ``Failed to fetch``，付款截图一张都传不上去。系统里其余图片（收款码、催发截图、
        7 天数据、品牌词）早就改成后端代传，这里同样处理。

        顺序：权限 → 结算单状态 → 图片校验 → 上传 → 复用 ``_mark_paid``（FB4 6 项校验 +
        状态推进 + 事件）→ commit。前三步都在上传之前：状态不对的单不该先往桶里放一个文件。
        上传之后任何一步失败，回滚并删掉已上传的对象。付款日期取北京时间当天（F4）。
        """
        from fastapi.concurrency import run_in_threadpool

        await self._check_proof_upload_permission(user)
        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")
        SettlementStatusMachine.assert_can_transition(
            from_state=settlement.settlement_status,
            to_state=SettlementStatus.PAID.value,
            action="mark_paid",
        )
        problem = check_image_payload(
            data=data,
            mime_type=mime_type,
            filename=filename,
            max_bytes=PROOF_MAX_SIZE_BYTES,
            label="付款截图",
        )
        # mime_type 为 None 时 check_image_payload 必然已给出原因；一并判断是为了把类型
        # 收窄成 str，后面上传要用它当 content_type
        if problem is not None or mime_type is None:
            raise InvalidPaymentProofImageError(problem or "付款截图格式无法识别")

        attachment_id: UUID | None = None
        attachment_key: str | None = None
        try:
            attachment, _ = await self._attachment_service.create_upload_record(
                session=self._session,
                tenant_id=user.tenant_id,
                created_by=user.id,
                bucket="private",
                purpose="settlement_proof",
                filename=filename,
                mime_type=mime_type,
                size_bytes=len(data),
            )
            attachment_id = attachment.id
            attachment_key = attachment.r2_key
            await run_in_threadpool(
                self._attachment_service.upload_bytes,
                data,
                bucket="private",
                key=attachment_key,
                content_type=mime_type,
            )
            await self._attachment_service.mark_uploaded(
                session=self._session,
                attachment_id=attachment_id,
                tenant_id=user.tenant_id,
            )
            updated = await self._mark_paid(
                settlement_id,
                SettlementPaymentProofRequest(payment_proof_attachment_id=attachment_id),
                user,
            )
            # commit 在 try 里：提交失败也要删掉对象。响应组装放在外面 —— 已经提交的
            # 结算单引用着这个对象，之后再出错也不能删
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
                        "settlement_proof_compensation_delete_failed",
                        extra={"attachment_id": str(attachment_id)},
                    )
            raise

        return await self._to_response(updated, user)

    async def _mark_paid(
        self,
        settlement_id: UUID,
        payload: SettlementPaymentProofRequest,
        user: User,
    ) -> Settlement:
        """标记已付款，不 commit（调用方决定事务边界）.

        FB4：attachment 6 项强校验（ProofAttachmentValidator）。
        FB5：mark_paid 同事务发 SettlementPaid 反向事件（通知类 — 失败不阻塞主流程）。
        """
        await self._check_proof_upload_permission(user)

        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")

        # F4（Q11、D5）：付款日期 = 北京时间的当天；请求里的 payment_date 忽略。
        # 不能用 date.today()：容器是 UTC，北京 0 ~ 8 点之间会记成前一天
        payment_date = get_today()

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
                "payment_date": payment_date,
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

        # 时间线（6.3）：settlement.paid，附件 = 付款凭证（读时过收款码规则，运营看不到）
        paid_payload: dict[str, Any] = {}
        if updated.payment_amount is not None:
            paid_payload["payment_amount"] = str(updated.payment_amount)
        paid_payload["payment_date"] = payment_date.isoformat()
        await self._append_line_event(
            updated,
            LineEventType.SETTLEMENT_PAID,
            actor_id=user.id,
            from_state=SettlementStatus.PENDING_FINANCE.value,
            to_state=SettlementStatus.PAID.value,
            attachment_id=payload.payment_proof_attachment_id,
            payload=paid_payload,
        )

        await self._audit.log(
            action="settlement.mark_paid",
            resource="settlement",
            resource_id=settlement_id,
            after={
                "payment_date": payment_date.isoformat(),
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
            payment_date=payment_date,
            paid_by=user.id,
        )
        try:
            await event_bus.dispatch(event, session=self._session)
        except Exception as exc:
            # 通知类事件失败不阻塞主流程；与 U04 review approve raise 不对称（FB5）
            log.exception("settlement_paid_dispatch_failed")
            try:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            except Exception:  # noqa: S110 Sentry 上报是 best-effort，其失败不得掩盖原始异常（上一行已 log.exception）
                pass
            await self._log_event_dispatch_failure(event, exc, user, blocking=False)
            # 不重新 raise — 让调用方照常 commit（mark_paid 主流程已成功）

        return updated

    # ============================================================
    # 状态推进：resubmit（已驳回 → 待付款，F6）
    # ============================================================

    async def resubmit(
        self,
        settlement_id: UUID,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        autocommit: bool = True,
    ) -> Settlement:
        """已驳回 → 待付款（F6），清空实付与调整说明，由财务重新填。

        没有 HTTP 调用方：推广单 R7 发 ``SettlementResubmitted``，finance 监听以 ``autocommit=False`` 调用，
        租户与操作人取事件字段。条件 UPDATE ``WHERE settlement_status = '已驳回'``，0 行（不在已驳回 /
        跨租户）→ 409，异常冒泡让推广单一起回滚。``settlement.resubmitted`` 由推广侧写。
        """
        settlement = await self._repo.get_by_id(settlement_id)
        if settlement is None:
            raise SettlementNotFoundError(f"结算单 {settlement_id} 不存在")

        SettlementStatusMachine.assert_can_transition(
            from_state=SettlementStatus.REJECTED.value,
            to_state=SettlementStatus.PENDING_PAYMENT.value,
            action="resubmit",
        )

        updated = await self._repo.update_state(
            settlement_id=settlement_id,
            tenant_id=tenant_id,
            from_state_value=SettlementStatus.REJECTED.value,
            to_state_value=SettlementStatus.PENDING_PAYMENT.value,
            extra_fields={"payment_amount": None, "payment_adjust_note": None},
        )
        if updated is None:
            raise StateTransitionConflictError(
                "结款单不在已驳回，不能重提，请刷新后重试",
                details={
                    "settlement_id": str(settlement_id),
                    "settlement_status": settlement.settlement_status,
                },
            )

        settlement_state_transitions_total.labels(
            from_state=SettlementStatus.REJECTED.value,
            to_state=SettlementStatus.PENDING_PAYMENT.value,
        ).inc()

        await self._audit.log(
            action="settlement.resubmit",
            resource="settlement",
            resource_id=settlement_id,
            after={
                "settlement_status": SettlementStatus.PENDING_PAYMENT.value,
                "payment_amount_cleared": True,
            },
            user_id=actor_id,
        )
        if autocommit:
            await self._session.commit()
        return updated

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
        # F2：类型「运费」过渡期保留可用：召回仍是旧流程，寄回运费在 PR-8 之前只能走这里。
        # PR-8 与 R33 同时停用（设计 10.3 PR-6「过渡期」）；枚举值照旧保留，存量行照常显示。

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

        await self._append_line_event(
            updated,
            LineEventType.SETTLEMENT_EXTRA_ITEM_ADDED,
            actor_id=user.id,
            payload={
                "item_type": payload.item_type.value,
                "item_amount": f"{item.amount:.2f}",
                "total_amount": str(updated.total_amount),
            },
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

    async def get_settlement(self, settlement_id: UUID, user: User) -> SettlementResponse:
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
        # 当前用户的矩阵视角整页共用一个（行级 ui、页级 import、字段规则）
        actor = await load_flow_actor(user.id, self._roles, self._perms)
        can_see_payment = can_read_field("settlement", "amount", actor.field_ctx)

        # PR 角色（不可见金额）→ 限自己提交的
        is_my_only = filters.is_my or not can_see_payment

        repo_filters = RepoSettlementListFilters(
            keyword=filters.keyword,
            settlement_status=(
                filters.settlement_status.value if filters.settlement_status else None
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

        # 单据信息一页一条查询（7.5）
        infos = await self._repo.promotion_doc_info(
            tenant_id=user.tenant_id, promotion_ids=list({s.promotion_id for s in items})
        )
        responses = [
            await self._to_response(
                s,
                user,
                include_extra_items=False,
                actor=actor,
                promotion_info=infos.get(s.promotion_id),
                view="list",
            )
            for s in items
        ]

        # 反范式富化：批量取款式编码/名称 + 博主昵称（对齐 final.xlsx 结款表）
        await self._enrich_display_fields(items, responses)

        # 页级「导入结款」（流程线 PR-5，7.5）：与 manual_settlement 上传同一判定（finance.settlement:pay）
        page_actions: dict[str, Any] = {}
        if can_import_write(actor.perms, "manual_settlement"):
            page_actions["import"] = {"state": "enabled"}

        return SettlementPage(
            items=responses,
            total=total,
            page=page,
            page_size=page_size,
            ui={"actions": page_actions},
        )

    async def _enrich_display_fields(self, items: Any, responses: list[SettlementResponse]) -> None:
        """批量 join 款式（style_code/style_name）+ 博主（nickname）填充展示列。"""
        from sqlalchemy import select as _select

        from app.modules.blogger.models import Blogger
        from app.modules.product.models import Style

        style_ids = {s.style_id for s in items if s.style_id}
        blogger_ids = {s.blogger_id for s in items if s.blogger_id}
        style_map: dict[Any, Any] = {}
        blogger_map: dict[Any, str] = {}
        if style_ids:
            rows = (
                await self._session.execute(
                    _select(Style.id, Style.style_code, Style.style_name).where(
                        Style.id.in_(style_ids)
                    )
                )
            ).all()
            style_map = {r.id: (r.style_code, r.style_name) for r in rows}
        if blogger_ids:
            # 与上面的 style 查询列数不同，用独立变量避免类型冲突
            brows = (
                await self._session.execute(
                    _select(Blogger.id, Blogger.nickname).where(Blogger.id.in_(blogger_ids))
                )
            ).all()
            blogger_map = {r.id: r.nickname for r in brows}
        for s, resp in zip(items, responses, strict=False):
            sc = style_map.get(s.style_id)
            if sc:
                resp.style_code, resp.style_name = sc
            resp.blogger_nickname = blogger_map.get(s.blogger_id)

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
        raw = await self._repo.daily_summary_as_of(tenant_id=user.tenant_id, date_value=target_date)

        def _bucket(status_label: str) -> AmountBucket:
            data = raw.get(status_label, {"count": 0, "total_amount": "0"})
            return AmountBucket(count=data["count"], total_amount=Decimal(data["total_amount"]))

        outstanding_count = sum(
            raw.get(s, {"count": 0})["count"] for s in ("待核查", "待付款", "待财务付款")
        )
        outstanding_amount = sum(
            (
                Decimal(raw.get(s, {"total_amount": "0"})["total_amount"])
                for s in ("待核查", "待付款", "待财务付款")
            ),
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
            return AmountBucket(count=data["count"], total_amount=Decimal(data["total_amount"]))

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

    async def _append_line_event(
        self,
        settlement: Settlement,
        event_type: LineEventType,
        *,
        actor_id: UUID,
        from_state: str | None = None,
        to_state: str | None = None,
        attachment_id: UUID | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """结款组事件（流程线 6.3）：挂在推广单这条线上，payload 总带 ``settlement_id``。

        同事务、不吞异常（6.4）：写不进去整个动作失败。``from_state`` / ``to_state`` 记结款单状态。
        """
        line_id = await self._repo.promotion_line_id(settlement.promotion_id)
        await LineEventRepository(self._session).append(
            tenant_id=settlement.tenant_id,
            line_id=line_id,
            event_type=event_type,
            actor_id=actor_id,
            promotion_id=settlement.promotion_id,
            from_state=from_state,
            to_state=to_state,
            attachment_id=attachment_id,
            payload={"settlement_id": str(settlement.id), **(payload or {})},
        )

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
            raise FieldPermissionDenied(field="payment_proof_attachment_id", entity="settlement")

    async def _to_response(
        self,
        settlement: Settlement,
        user: User,
        *,
        include_extra_items: bool = True,
        actor: FlowActor | None = None,
        promotion_info: SettlementPromotionInfo | None = None,
        view: View = "detail",
    ) -> SettlementResponse:
        """组装响应：字段权限过滤 + attachment 签名 URL + 单据信息 + 矩阵 ``ui``.

        非 PAYMENT_VISIBLE_ROLES 角色 → amount / total_amount / payment_amount /
        payment_proof_signed_url 全部置 None。

        Args:
            actor: 当前用户的矩阵视角（列表整页共用一个）；None 时现查。字段权限上下文也取它的。
            promotion_info: 列表整页一次查好的单据信息（7.5）；None 时单条现查。
            view: ``ui`` 的形状，列表 ``list``，其余 ``detail``。
        """
        if actor is None:
            actor = await load_flow_actor(user.id, self._roles, self._perms)
        ctx = actor.field_ctx
        can_see_amount = can_read_field("settlement", "amount", ctx)
        can_see_total = can_read_field("settlement", "total_amount", ctx)
        can_see_payment_amount = can_read_field("settlement", "payment_amount", ctx)
        can_see_adjust_note = can_read_field("settlement", "payment_adjust_note", ctx)
        # 签名 URL 可见性沿用金额可见口径（amount）
        can_see_payment = can_see_amount

        # 签名 URL（仅可见金额角色 + 已有 attachment 时生成）
        signed_url: str | None = None
        if can_see_payment and settlement.payment_proof_attachment_id is not None:
            attachment = await self._attachment_service.get_by_id(
                session=self._session,
                attachment_id=settlement.payment_proof_attachment_id,
            )
            if attachment is not None and attachment.status == "ready":
                try:
                    signed_url = self._attachment_service.get_signed_url(
                        "private", attachment.r2_key, expires_in=900
                    )
                except Exception:
                    log.warning("signed_url_generation_failed")

        extra_items: list[SettlementExtraItemResponse] = []
        if include_extra_items:
            raw_items = await self._repo.list_extra_items(settlement_id=settlement.id)
            extra_items = [SettlementExtraItemResponse.model_validate(it) for it in raw_items]

        # 单据信息（7.5 / 7d-3）：收款码签名跟字段规则 promotion.payment_qr（含财务，4.6）
        info = promotion_info
        if info is None:
            info = (
                await self._repo.promotion_doc_info(
                    tenant_id=settlement.tenant_id, promotion_ids=[settlement.promotion_id]
                )
            ).get(settlement.promotion_id)
        payment_qr_url: str | None = None
        if (
            info is not None
            and can_read_field("promotion", "payment_qr", ctx)
            and info.payment_qr_status == "ready"
            and info.payment_qr_r2_key
        ):
            try:
                payment_qr_url = self._attachment_service.get_signed_url(
                    "private", info.payment_qr_r2_key, expires_in=900
                )
            except Exception:
                log.warning("settlement_payment_qr_signed_url_failed")

        ui = ui_for(
            actor,
            build_settlement_doc(status=settlement.settlement_status, pr_id=settlement.pr_id),
        ).to_dict(view=view)

        return SettlementResponse(
            id=settlement.id,
            settlement_no=settlement.settlement_no,
            promotion_id=settlement.promotion_id,
            blogger_id=settlement.blogger_id,
            style_id=settlement.style_id,
            pr_id=settlement.pr_id,
            amount=settlement.amount if can_see_amount else None,
            total_amount=settlement.total_amount if can_see_total else None,
            payment_amount=(settlement.payment_amount if can_see_payment_amount else None),
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
            payment_adjust_note=settlement.payment_adjust_note if can_see_adjust_note else None,
            promotion_internal_code=info.internal_code if info is not None else None,
            cooperation_mode=info.cooperation_mode if info is not None else None,
            publish_url=info.publish_url if info is not None else None,
            promotion_remark=info.remark if info is not None else None,
            promotion_review_reason=info.review_reason if info is not None else None,
            promotion_resubmit_note=info.resubmit_note if info is not None else None,
            payment_qr_signed_url=payment_qr_url,
            display_short_name=info.display_short_name if info is not None else None,
            line_id=info.line_id if info is not None else None,
            extra_items=extra_items,
            ui=ui,
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
            except Exception as audit_exc:
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
