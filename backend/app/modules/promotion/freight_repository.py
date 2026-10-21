"""R33 SQL：租户限定、同推广唯一、条件更新；事务由service持有。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.blogger.models import Blogger
from app.modules.product.goods_models import GoodsMain
from app.modules.promotion.freight_models import ReturnFreightPayment as Freight
from app.modules.promotion.freight_schemas import FreightListFilters
from app.modules.promotion.models import Promotion


@dataclass(frozen=True)
class FreightReadRecord:
    freight: Freight
    promotion: Promotion
    blogger_nickname: str
    goods_short_name: str | None


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

    async def read_records(
        self,
        *,
        tenant_id: UUID,
        filters: FreightListFilters,
        ids: Sequence[UUID] | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[FreightReadRecord], int]:
        stmt = (
            select(Freight, Promotion, Blogger.nickname, GoodsMain.short_name)
            .join(
                Promotion,
                and_(
                    Promotion.id == Freight.promotion_id, Promotion.tenant_id == Freight.tenant_id
                ),
            )
            .join(
                Blogger,
                and_(Blogger.id == Promotion.blogger_id, Blogger.tenant_id == Promotion.tenant_id),
            )
            .outerjoin(
                GoodsMain,
                and_(
                    GoodsMain.id == Promotion.goods_main_id,
                    GoodsMain.tenant_id == Promotion.tenant_id,
                ),
            )
            .where(Freight.tenant_id == tenant_id)
        )
        if ids is not None:
            stmt = stmt.where(Freight.id.in_(ids))
        if filters.status is not None:
            stmt = stmt.where(Freight.status == filters.status)
        if filters.promotion_id is not None:
            stmt = stmt.where(Freight.promotion_id == filters.promotion_id)
        if filters.keyword:
            needle = (
                "%"
                + filters.keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                + "%"
            )
            stmt = stmt.where(
                or_(
                    Promotion.internal_code.ilike(needle, escape="\\"),
                    Blogger.nickname.ilike(needle, escape="\\"),
                )
            )
        if filters.payment_date_from:
            stmt = stmt.where(Freight.payment_date >= filters.payment_date_from)
        if filters.payment_date_to:
            stmt = stmt.where(Freight.payment_date <= filters.payment_date_to)
        total = int(
            (
                await self._session.execute(select(func.count()).select_from(stmt.subquery()))
            ).scalar_one()
        )
        rows = (
            await self._session.execute(
                stmt.order_by(Freight.submitted_at.desc(), Freight.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return [FreightReadRecord(*row) for row in rows], total
