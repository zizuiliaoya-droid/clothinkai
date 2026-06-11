"""U16 拍单/刷单 + 余额流水仓储。

RLS 自动隔离；last_balance / 聚合显式 WHERE tenant_id（bypass 角色防御）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.finance.order_adjustment_models import (
    BalanceRecord,
    OrderAdjustment,
)


class OrderAdjustmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, row: OrderAdjustment) -> None:
        self._s.add(row)

    async def get_by_promotion(
        self, promotion_id: UUID
    ) -> OrderAdjustment | None:
        stmt = select(OrderAdjustment).where(
            OrderAdjustment.promotion_id == promotion_id
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def exists_order_no(self, order_no: str) -> bool:
        stmt = (
            select(func.count())
            .select_from(OrderAdjustment)
            .where(OrderAdjustment.order_no == order_no)
        )
        return int((await self._s.execute(stmt)).scalar_one()) > 0

    async def list(
        self,
        *,
        order_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[OrderAdjustment]:
        stmt = select(OrderAdjustment)
        if order_type is not None:
            stmt = stmt.where(OrderAdjustment.order_type == order_type)
        stmt = (
            stmt.order_by(OrderAdjustment.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._s.execute(stmt)).scalars().all()


class BalanceRecordRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def add(self, row: BalanceRecord) -> None:
        self._s.add(row)

    async def last_balance(self, tenant_id: UUID) -> Decimal:
        stmt = (
            select(BalanceRecord.balance_after)
            .where(BalanceRecord.tenant_id == tenant_id)
            .order_by(BalanceRecord.created_at.desc())
            .limit(1)
        )
        val = (await self._s.execute(stmt)).scalar_one_or_none()
        return val if val is not None else Decimal("0")

    async def list(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[BalanceRecord]:
        stmt = select(BalanceRecord)
        if date_from is not None:
            stmt = stmt.where(BalanceRecord.record_date >= date_from)
        if date_to is not None:
            stmt = stmt.where(BalanceRecord.record_date <= date_to)
        stmt = (
            stmt.order_by(BalanceRecord.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return (await self._s.execute(stmt)).scalars().all()


__all__ = ["BalanceRecordRepository", "OrderAdjustmentRepository"]
