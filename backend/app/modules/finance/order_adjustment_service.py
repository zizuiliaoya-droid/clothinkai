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
        rows = await self._repo.list(
            order_type=order_type, limit=limit, offset=offset
        )
        # 反范式富化：批量取款式编码/名称（对齐 final.xlsx 拍单/刷单的「款式/款号」）
        from sqlalchemy import select as _select

        from app.modules.product.models import Style

        style_ids = {r.style_id for r in rows if getattr(r, "style_id", None)}
        style_map: dict = {}
        if style_ids:
            srows = (
                await self._session.execute(
                    _select(Style.id, Style.style_code, Style.style_name).where(
                        Style.id.in_(style_ids)
                    )
                )
            ).all()
            style_map = {s.id: (s.style_code, s.style_name) for s in srows}
        result = []
        for r in rows:
            sc = style_map.get(getattr(r, "style_id", None))
            result.append(
                {
                    "id": r.id,
                    "order_type": r.order_type,
                    "order_date": r.order_date,
                    "order_no": r.order_no,
                    "style_id": r.style_id,
                    "sku_id": r.sku_id,
                    "style_code": sc[0] if sc else None,
                    "style_name": sc[1] if sc else None,
                    "blogger_identifier": r.blogger_identifier,
                    "amount": r.amount,
                    "exclude_from_roi": r.exclude_from_roi,
                    "status": r.status,
                    "promotion_id": r.promotion_id,
                    "remark": r.remark,
                }
            )
        return result


__all__ = ["OrderAdjustmentService", "parse_amount_expr"]
