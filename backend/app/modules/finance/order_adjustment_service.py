"""U16 OrderAdjustmentService（拍单自动生成 + 刷单录入 + 金额表达式解析）。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.metrics import order_adjustment_auto_created_total
from app.modules.finance.enums import OrderAdjustmentStatus, OrderType
from app.modules.finance.exceptions import AmountExpressionInvalidError
from app.modules.finance.order_adjustment_models import OrderAdjustment
from app.modules.finance.order_adjustment_repository import (
    OrderAdjustmentRepository,
)
from app.modules.finance.order_adjustment_schemas import BrushingCreate

_NUM = r"\d+(?:\.\d{1,2})?"
_EXPR = re.compile(rf"^\s*({_NUM})\s*(?:-\s*({_NUM}))?\s*$")


def parse_amount_expr(raw: str | Decimal) -> Decimal:
    """解析金额："数字" 或 "原价-返现"（如 "100-30" → 70）。不使用 eval。"""
    if isinstance(raw, Decimal):
        return raw
    m = _EXPR.match(str(raw))
    if not m:
        raise AmountExpressionInvalidError(f"非法金额格式: {raw}")
    try:
        base = Decimal(m.group(1))
        rebate = Decimal(m.group(2)) if m.group(2) else Decimal("0")
    except InvalidOperation as exc:
        raise AmountExpressionInvalidError(f"非法金额: {raw}") from exc
    amount = base - rebate
    if amount < 0:
        raise AmountExpressionInvalidError(f"金额不能为负: {raw}")
    return amount


class OrderAdjustmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = OrderAdjustmentRepository(session)

    async def auto_create_from_promotion(
        self, promo: Any
    ) -> OrderAdjustment | None:
        """EP06-S09：promotion.in_store_order=true 时自动生成拍单（幂等）。"""
        existing = await self._repo.get_by_promotion(promo.id)
        if existing is not None:
            order_adjustment_auto_created_total.labels(result="skipped").inc()
            return existing
        row = OrderAdjustment(
            order_type=OrderType.STORE_ORDER.value,
            order_date=promo.cooperation_date,
            style_id=promo.style_id,
            sku_id=getattr(promo, "sku_id", None),
            blogger_identifier=str(promo.blogger_id),
            promotion_id=promo.id,
            amount=Decimal("0"),
            exclude_from_roi=False,
            status=OrderAdjustmentStatus.PENDING_PAYMENT.value,
        )
        self._repo.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            order_adjustment_auto_created_total.labels(result="skipped").inc()
            return None
        order_adjustment_auto_created_total.labels(result="created").inc()
        return row

    async def create_brushing(
        self, payload: BrushingCreate, user: Any
    ) -> dict:
        """EP06-S10：刷单录入，exclude_from_roi 默认 true，金额表达式解析。"""
        amount = parse_amount_expr(payload.amount_expr)
        duplicate = False
        if payload.order_no:
            duplicate = await self._repo.exists_order_no(payload.order_no)
        row = OrderAdjustment(
            order_type=OrderType.BRUSHING.value,
            order_date=payload.order_date,
            order_no=payload.order_no,
            style_id=payload.style_id,
            sku_id=payload.sku_id,
            blogger_identifier=payload.blogger_identifier,
            amount=amount,
            exclude_from_roi=True,
            status=OrderAdjustmentStatus.PENDING_PAYMENT.value,
            remark=payload.remark,
        )
        self._repo.add(row)
        await self._session.flush()
        await AuditService(self._session).log(
            "finance.order.brushing_create",
            resource="order_adjustment",
            resource_id=row.id,
            user_id=user.id,
        )
        await self._session.commit()
        return {
            "id": row.id,
            "order_type": row.order_type,
            "amount": amount,
            "exclude_from_roi": True,
            "status": row.status,
            "order_no": row.order_no,
            "duplicate": duplicate,
        }

    async def list(
        self, *, order_type: str | None = None, limit: int = 50, offset: int = 0
    ):
        return await self._repo.list(
            order_type=order_type, limit=limit, offset=offset
        )


__all__ = ["OrderAdjustmentService", "parse_amount_expr"]
