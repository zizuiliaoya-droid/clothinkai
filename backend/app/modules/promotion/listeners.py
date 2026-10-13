"""U04 promotion 事件监听器（通知类反向，U05 实施时新建）。

按 U05 nfr-design-patterns.md P-U05-04 + functional-design BR-U05-82 实施。

``on_settlement_paid``：监听 U05 的 SettlementPaid（通知类 FB5）：
- U05 → U04 反向同步 promotion.settlement_status='已付款'
- UPDATE WHERE 旧状态='待付款'（FB7 模式）+ tenant_id 防护
- 0 行匹配（已被推进 / 跨租户 / 状态不符）→ **不抛错**，仅 log + 指标（FB5 通知类）

与 U05 finance.listeners.on_settlement_requested（强一致）形成不对称：
- SettlementRequested 失败 → 冒泡 → 事务回滚
- SettlementPaid 失败 → 不抛错（通知类可丢，V1 reconcile 兜底）

注册位置：main.py register_event_listeners 第 2 步（通知类，缺失只 warning）。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import subscribe
from app.core.metrics import settlement_paid_sync_no_match_total
from app.modules.finance.enums import SettlementStatus as SettlementDocStatus
from app.modules.finance.events import SettlementPaid, SettlementRejected
from app.modules.flow.line_events import LineEventRepository, LineEventType
from app.modules.promotion.enums import SettlementStatus
from app.modules.promotion.exceptions import StateTransitionConflictError
from app.modules.promotion.repository import PromotionRepository
from app.modules.promotion.state_machines import SettlementStatusMachine

log = logging.getLogger(__name__)


async def on_settlement_paid(event: SettlementPaid, session: AsyncSession) -> None:
    """U05 → U04 反向同步：promotion.settlement_status='已付款'（通知类 FB5）。

    UPDATE WHERE id + tenant_id + 旧 settlement_status='待付款'（FB7 模式）。
    0 行匹配时不抛错（promotion 状态已变更 / 跨租户 / 软删）— 通知类容忍。
    """
    repo = PromotionRepository(session)
    updated = await repo.update_state(
        promotion_id=event.promotion_id,
        tenant_id=event.tenant_id,
        from_state_field="settlement_status",
        from_state_value=SettlementStatus.PENDING_PAYMENT.value,  # U04 端的"待付款"
        to_state_value=SettlementStatus.PAID.value,
    )
    if updated is None:
        # 已被推进 / 跨租户 / 软删除；不抛错（FB5 通知类）
        settlement_paid_sync_no_match_total.inc()
        log.warning(
            "settlement_paid_sync_no_match",
            extra={
                "promotion_id": str(event.promotion_id),
                "settlement_id": str(event.settlement_id),
            },
        )
    else:
        log.info(
            "settlement_paid_synced",
            extra={"promotion_id": str(event.promotion_id)},
        )


async def on_settlement_rejected(event: SettlementRejected, session: AsyncSession) -> None:
    """财务驳回结款单（F5）→ 推广单 待付款 → 结款驳回（R6，流程线 3.4、7.5）。

    强一致（与 ``on_settlement_paid`` 不同）：条件 UPDATE ``WHERE settlement_status = '待付款'``，
    0 行（推广单不在待付款，例如导入来的结款单）→ 409 ``PROMOTION_STATE_CONFLICT`` 冒泡，结款单一起回滚。
    同事务写 ``settlement.rejected``（驳回原因进 note，from / to 记结款单状态）；不 commit、不吞异常。
    """
    SettlementStatusMachine.assert_can_transition(
        from_state=SettlementStatus.PENDING_PAYMENT,
        to_state=SettlementStatus.SETTLEMENT_REJECTED,
        action="settlement_reject",
    )
    updated = await PromotionRepository(session).update_state(
        promotion_id=event.promotion_id,
        tenant_id=event.tenant_id,
        from_state_field="settlement_status",
        from_state_value=SettlementStatus.PENDING_PAYMENT.value,
        to_state_value=SettlementStatus.SETTLEMENT_REJECTED.value,
    )
    if updated is None:
        raise StateTransitionConflictError(
            "推广单不在待财务付款，不能驳回这张结款单",
            details={
                "promotion_id": str(event.promotion_id),
                "settlement_id": str(event.settlement_id),
            },
        )
    await LineEventRepository(session).append(
        tenant_id=event.tenant_id,
        line_id=updated.line_id,
        event_type=LineEventType.SETTLEMENT_REJECTED,
        actor_id=event.actor_id,
        promotion_id=event.promotion_id,
        from_state=event.from_state,
        to_state=SettlementDocStatus.REJECTED.value,
        note=event.reason,
        payload={"settlement_id": str(event.settlement_id)},
    )


def register() -> None:
    """U04 端 listener 注册：SettlementPaid（通知类反向）+ SettlementRejected（强一致，R6）。

    被 main.py register_event_listeners 第 2 步调用。
    SettlementPaid 缺失不阻塞（required_handler=False，FB5）；SettlementRejected 缺失时财务驳回
    抛 ``MissingRequiredHandlerError``。
    """
    subscribe("SettlementPaid", on_settlement_paid)
    subscribe("SettlementRejected", on_settlement_rejected)


__all__ = ["on_settlement_paid", "on_settlement_rejected", "register"]
