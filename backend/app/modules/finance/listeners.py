"""U05 finance 事件监听器（强一致正向）。

按 nfr-design-patterns.md P-U05-04 + functional-design business-rules BR-U05-10 实施。

``on_settlement_requested``：监听 U04 的 SettlementRequested（强一致 FB1）：
- 同事务执行（与 U04 service.review approve 共享 session）
- 三重幂等（DB UNIQUE × 2 + service SELECT，FB1+FB3）
- handler 内 ``await session.flush()``（FB6：UNIQUE / FK 错误立即暴露）
- settlement_status 起点 = "待核查"（FB1）

注册位置：main.py register_event_listeners 第 1 步（强一致，缺失 fail fast）。
"""

from __future__ import annotations

import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.events import subscribe
from app.core.metrics import settlement_created_via_event_total
from app.modules.auth.models import Tenant
from app.modules.finance.domain import format_settlement_no
from app.modules.finance.enums import SettlementStatus
from app.modules.finance.models import Settlement
from app.modules.finance.repository import SettlementRepository
from app.modules.promotion.events import SettlementRequested

log = logging.getLogger(__name__)


async def on_settlement_requested(
    event: SettlementRequested, session: AsyncSession
) -> None:
    """同事务 handler：失败抛异常导致 U04 端事务回滚（FB1 强一致）。

    三重幂等（FB1+FB3）：
    1. DB UNIQUE(tenant_id, promotion_id) 永久 — DB 层兜底
    2. DB UNIQUE(request_event_id) 永久 — 事件重放兜底
    3. service SELECT 检查 — 友好错误 + audit 区分（本方法实施）
    """
    repo = SettlementRepository(session)
    audit = AuditService(session)

    # 1. 幂等检查（service 层 SELECT 兜底）
    existing = await repo.find_by_promotion_id(event.promotion_id)
    if existing is not None:
        settlement_created_via_event_total.labels(
            result="duplicate_skipped"
        ).inc()
        await audit.log(
            action="settlement.create_skipped_duplicate",
            resource="settlement",
            resource_id=existing.id,
            user_id=event.requested_by,
            after={
                "event_id": str(event.event_id),
                "existing_settlement_id": str(existing.id),
                "promotion_id": str(event.promotion_id),
            },
        )
        return

    # 2. 序列号原子分配（FB2，复用 U04 模式）
    seq = await repo.next_settlement_sequence(
        tenant_id=event.tenant_id,
        date_key=event.requested_at.date(),
    )

    # 3. tenant_code 取数 + 格式化 settlement_no
    tenant_code = await _get_tenant_code(session, event.tenant_id)
    settlement_no = format_settlement_no(
        tenant_code=tenant_code,
        date_key=event.requested_at.date(),
        sequence=seq,
    )

    # 4. 创建实体 — settlement_status 起点 = 待核查（FB1）
    settlement = Settlement(
        id=uuid4(),
        tenant_id=event.tenant_id,
        promotion_id=event.promotion_id,
        blogger_id=event.blogger_id,
        style_id=event.style_id,
        pr_id=event.pr_id,
        settlement_no=settlement_no,
        amount=event.amount,
        total_amount=event.amount,  # 初始无 extra_item
        settlement_status=SettlementStatus.PENDING_REVIEW.value,
        request_event_id=event.event_id,
    )
    repo.add(settlement)

    # 5. 立即 flush（FB6：UNIQUE / FK 错误在 dispatch 阶段就暴露，不延迟到外层 commit）
    try:
        await session.flush()
    except Exception:
        settlement_created_via_event_total.labels(result="error").inc()
        raise

    # 6. 写 audit（脱敏：amount 仅记 *_changed: true）
    await audit.log(
        action="settlement.create_via_event",
        resource="settlement",
        resource_id=settlement.id,
        user_id=event.requested_by,
        after={
            "settlement_no": settlement_no,
            "promotion_id": str(event.promotion_id),
            "amount_changed": True,
            "total_amount_changed": True,
            "settlement_status": SettlementStatus.PENDING_REVIEW.value,
        },
    )

    settlement_created_via_event_total.labels(result="created").inc()


async def _get_tenant_code(session: AsyncSession, tenant_id) -> str:
    """取 tenant.code 用于 settlement_no 前缀（与 U04 service._get_tenant_code 一致）。"""
    result = await session.execute(
        select(Tenant.code).where(Tenant.id == tenant_id)
    )
    code = result.scalar_one_or_none()
    return str(code or "")


async def on_settlement_requested_auto_order(
    event: SettlementRequested, session: AsyncSession
) -> None:
    """U16 EP06-S09：审核通过时若 promotion.in_store_order=true 自动生成拍单。

    与 on_settlement_requested 同事件多 handler（U05 在前创建 settlement，U16 在后）。
    best-effort：拍单创建失败 catch + log + 指标，不冒泡（不阻塞 settlement 创建）。
    """
    from app.core.metrics import order_adjustment_auto_created_total
    from app.modules.finance.order_adjustment_service import (
        OrderAdjustmentService,
    )
    from app.modules.promotion.repository import PromotionRepository

    try:
        promo = await PromotionRepository(session).get_by_id(event.promotion_id)
        if promo is None or not getattr(promo, "in_store_order", False):
            return
        await OrderAdjustmentService(session).auto_create_from_promotion(promo)
    except Exception as exc:  # noqa: BLE001 — best-effort，不阻塞 settlement 创建
        log.warning(
            "auto_order_create_failed",
            extra={"promotion_id": str(event.promotion_id), "err": str(exc)},
        )
        order_adjustment_auto_created_total.labels(result="failed").inc()


def register() -> None:
    """U05 finance listener 注册（强一致正向）+ U16 拍单自动生成（通知类）。

    被 main.py register_event_listeners 第 1 步调用。
    缺失时 main.py 视为 U05 未部署（warning），但因 SettlementRequested
    required_handler=True，U04 review approve 会抛 MissingRequiredHandlerError（FB1）。
    """
    subscribe("SettlementRequested", on_settlement_requested)
    subscribe("SettlementRequested", on_settlement_requested_auto_order)


__all__ = [
    "on_settlement_requested",
    "on_settlement_requested_auto_order",
    "register",
]
