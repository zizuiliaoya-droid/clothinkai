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
from app.modules.finance.events import SettlementPaid
from app.modules.promotion.enums import SettlementStatus
from app.modules.promotion.repository import PromotionRepository

log = logging.getLogger(__name__)


async def on_settlement_paid(
    event: SettlementPaid, session: AsyncSession
) -> None:
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


def register() -> None:
    """U04 端 SettlementPaid listener 注册（通知类反向）。

    被 main.py register_event_listeners 第 2 步调用。
    缺失时 main.py 仅 warning（不阻塞，因 SettlementPaid required_handler=False，FB5）。
    """
    subscribe("SettlementPaid", on_settlement_paid)


__all__ = ["on_settlement_paid", "register"]
