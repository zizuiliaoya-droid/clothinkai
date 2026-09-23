"""U16 拍单/刷单 + 余额流水仓储。

RLS 自动隔离；last_balance / 聚合显式 WHERE tenant_id（bypass 角色防御）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, nullslast, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.finance.order_adjustment_models import (
    BalanceRecord,
    OrderAdjustment,
)
from app.modules.finance.order_adjustment_schemas import OrderAdjustmentListFilters


class OrderAdjustmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def count_by_sku(self, sku_id: UUID) -> int:
        """统计 SKU 的全部历史订单调整引用；租户隔离由 RLS 保证。"""
        stmt = (
            select(func.count())
            .select_from(OrderAdjustment)
            .where(OrderAdjustment.sku_id == sku_id)
        )
        return int((await self._s.execute(stmt)).scalar_one())

    def add(self, row: OrderAdjustment) -> None:
        self._s.add(row)

    async def get_by_promotion(self, promotion_id: UUID) -> OrderAdjustment | None:
        stmt = select(OrderAdjustment).where(OrderAdjustment.promotion_id == promotion_id)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def exists_order_no(self, order_no: str) -> bool:
        stmt = (
            select(func.count())
            .select_from(OrderAdjustment)
            .where(OrderAdjustment.order_no == order_no)
        )
        return int((await self._s.execute(stmt)).scalar_one()) > 0

    async def get_by_id(self, row_id: UUID) -> OrderAdjustment | None:
        return await self._s.get(OrderAdjustment, row_id)

    async def list_paginated(
        self,
        *,
        filters: OrderAdjustmentListFilters,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[Sequence[OrderAdjustment], int]:
        """筛选 + 分页列表，返回 ``(rows, total)``。

        合并页面后拍单与刷单同表展示，条数翻倍；原来的 ``limit`` 无分页写法
        会让超出上限的单据直接看不到。
        """
        stmt = select(OrderAdjustment)
        if filters.order_type is not None:
            stmt = stmt.where(OrderAdjustment.order_type == filters.order_type)
        if filters.status is not None:
            stmt = stmt.where(OrderAdjustment.status == filters.status)
        if filters.style_id is not None:
            stmt = stmt.where(OrderAdjustment.style_id == filters.style_id)
        if filters.order_date_from is not None:
            stmt = stmt.where(OrderAdjustment.order_date >= filters.order_date_from)
        if filters.order_date_to is not None:
            stmt = stmt.where(OrderAdjustment.order_date <= filters.order_date_to)
        if filters.amount_min is not None:
            stmt = stmt.where(OrderAdjustment.amount >= filters.amount_min)
        if filters.amount_max is not None:
            stmt = stmt.where(OrderAdjustment.amount <= filters.amount_max)
        if filters.exclude_from_roi is not None:
            stmt = stmt.where(OrderAdjustment.exclude_from_roi.is_(filters.exclude_from_roi))
        if filters.has_payment_qr is not None:
            if filters.has_payment_qr:
                stmt = stmt.where(OrderAdjustment.payment_qr_attachment_id.is_not(None))
            else:
                stmt = stmt.where(OrderAdjustment.payment_qr_attachment_id.is_(None))
        if filters.keyword:
            pattern = f"%{filters.keyword}%"
            stmt = stmt.where(
                or_(
                    OrderAdjustment.order_no.ilike(pattern),
                    OrderAdjustment.blogger_identifier.ilike(pattern),
                )
            )

        total = int(
            (await self._s.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
        )
        # order_date 可空，空值排最后；再用 created_at 保证同日内稳定有序
        stmt = (
            stmt.order_by(
                nullslast(OrderAdjustment.order_date.desc()),
                OrderAdjustment.created_at.desc(),
            )
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        return (await self._s.execute(stmt)).scalars().all(), total


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
        stmt = stmt.order_by(BalanceRecord.created_at.asc()).limit(limit).offset(offset)
        return (await self._s.execute(stmt)).scalars().all()


__all__ = ["BalanceRecordRepository", "OrderAdjustmentRepository"]
