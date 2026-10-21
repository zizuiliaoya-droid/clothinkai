"""R33 SQL：租户限定、同推广唯一、条件更新；事务由service持有。"""

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.promotion.freight_models import ReturnFreightPayment as Freight


class ReturnFreightRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, freight_id: UUID, *, tenant_id: UUID) -> Freight | None:
        return (
            await self._session.execute(
                select(Freight).where(Freight.id == freight_id, Freight.tenant_id == tenant_id)
            )
        ).scalar_one_or_none()

    async def by_promotion(
        self, promotion_id: UUID, *, tenant_id: UUID, for_update: bool = False
    ) -> Freight | None:
        stmt = select(Freight).where(
            Freight.promotion_id == promotion_id, Freight.tenant_id == tenant_id
        )
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def status_for_promotion(self, promotion_id: UUID, *, tenant_id: UUID) -> str | None:
        return (
            await self._session.execute(
                select(Freight.status).where(
                    Freight.promotion_id == promotion_id, Freight.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()

    async def briefs(
        self, promotion_ids: Sequence[UUID], *, tenant_id: UUID
    ) -> dict[UUID, Freight]:
        if not promotion_ids:
            return {}
        rows = (
            await self._session.execute(
                select(Freight).where(
                    Freight.tenant_id == tenant_id, Freight.promotion_id.in_(promotion_ids)
                )
            )
        ).scalars()
        return {row.promotion_id: row for row in rows}

    async def insert_once(self, values: Mapping[str, Any]) -> Freight | None:
        stmt = (
            insert(Freight)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[Freight.tenant_id, Freight.promotion_id])
            .returning(Freight)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def transition(
        self,
        freight_id: UUID,
        *,
        tenant_id: UUID,
        from_status: str,
        to_status: str,
        values: Mapping[str, Any],
    ) -> Freight | None:
        stmt = (
            update(Freight)
            .where(
                Freight.id == freight_id,
                Freight.tenant_id == tenant_id,
                Freight.status == from_status,
            )
            .values(status=to_status, **values)
            .returning(Freight)
            .execution_options(populate_existing=True)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
