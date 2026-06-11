"""U14 StoreDailyService（店铺数据看板聚合 + 手动字段 upsert）。"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditService
from app.core.metrics import report_query_duration_seconds
from app.modules.auth.models import User
from app.modules.report.advanced_repository import StoreDailyRepository
from app.modules.report.advanced_schemas import (
    StoreDailyManualUpdate,
    StoreDailyRow,
)
from app.modules.report.work_progress_models import StoreDaily


class StoreDailyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = StoreDailyRepository(session)
        self._audit = AuditService(session)

    async def get_dashboard(
        self, tenant_id: UUID, time_range: tuple[date, date]
    ) -> list[StoreDailyRow]:
        with report_query_duration_seconds.labels("store_daily").time():
            rows = await self._repo.aggregate(
                tenant_id=tenant_id,
                date_from=time_range[0],
                date_to=time_range[1],
            )
        return [self._to_row(r) for r in rows]

    @staticmethod
    def _to_row(r: Mapping[str, Any]) -> StoreDailyRow:
        return StoreDailyRow(
            date=r["date"],
            visitors=int(r["visitors"]),
            pay_amount=r["pay_amount"],
            pay_orders=int(r["pay_orders"]),
            ad_spend_total=r["ad_spend_total"],
            zhitongche_spend=r["zhitongche_spend"],
            yinli_spend=r["yinli_spend"],
        )

    async def upsert_manual(
        self,
        tenant_id: UUID,
        day: date,
        payload: StoreDailyManualUpdate,
        user: User,
    ) -> StoreDaily:
        set_fields = {
            k: v
            for k, v in payload.model_dump(exclude_unset=True).items()
            if v is not None
        }
        stmt = (
            pg_insert(StoreDaily)
            .values(tenant_id=tenant_id, date=day, **set_fields)
            .on_conflict_do_update(
                index_elements=["tenant_id", "date"],
                set_={**set_fields, "updated_at": func.now()},
            )
            .returning(StoreDaily)
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one()
        await self._audit.log(
            action="report.store_daily.update",
            resource="store_daily",
            resource_id=row.id,
            after={"date": str(day), **{k: str(v) for k, v in set_fields.items()}},
            user_id=user.id,
        )
        await self._session.commit()
        return row


__all__ = ["StoreDailyService"]
